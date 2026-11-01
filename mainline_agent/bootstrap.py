"""Portable setup using existing hardware planning and local Ollama validation."""
import argparse
import json
import math
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .cli import hardware_options, plan_for
from .llm import Ollama
from .report import atomic_json
from .process_env import provisioning_environment


MODEL_GIB = {'qwen2.5-coder:7b': 5, 'qwen2.5-coder:14b': 9, 'qwen3-coder:30b': 19}


def installer_help():
    system = platform.system()
    if system == 'Darwin':
        return 'Install Ollama for macOS, then launch the Ollama application.'
    if system == 'Windows':
        return 'Install Ollama for Windows, then launch Ollama and reopen this terminal.'
    return 'Install Ollama for your operating system, then start its local service.'


def smoke_test(client):
    body = {'model': client.plan['model'], 'stream': False, 'keep_alive': 0,
            'messages': [{'role': 'user', 'content': 'Reply with the single word READY.'}],
            'options': {'num_ctx': client.plan['context'], 'num_predict': 32, 'temperature': 0}}
    if client.disable_thinking:
        body['think'] = False
    result = client.request('/api/chat', body)
    message = result.get('message')
    if result.get('done') is not True or result.get('done_reason') == 'length' or not isinstance(message, dict) or not isinstance(message.get('content'), str) or not message['content'].strip():
        raise ValueError('Local inference smoke test did not complete with a text response.')


def setup(args):
    plan = plan_for(args)
    client = Ollama(plan, args.ollama_url)
    print('Selected local model: %s, context %s, hardware tier %s.' % (plan['model'], plan['context'], plan['tier']), flush=True)
    try:
        models = client.request('/api/tags').get('models')
    except ValueError as exc:
        raise ValueError(installer_help() + ' If already installed, start ollama serve on loopback. ' + str(exc)) from exc
    if not isinstance(models, list) or not all(isinstance(model, dict) for model in models):
        raise ValueError('Invalid local Ollama model inventory.')
    if not any(model.get('name') == plan['model'] for model in models):
        if args.offline:
            raise ValueError('Selected weights are missing. Offline setup cannot download them.')
        binary = shutil.which('ollama')
        if not binary:
            raise ValueError(installer_help() + ' The ollama executable must be available on PATH to download weights.')
        budget = args.required_free_gb
        if budget is None:
            if plan['model'] not in MODEL_GIB:
                raise ValueError('Custom model size is unknown. Set --required-free-gb after checking its disk requirements.')
            budget = MODEL_GIB[plan['model']] * 2 + 2
        if not math.isfinite(budget) or budget <= 0:
            raise ValueError('--required-free-gb must be positive and finite.')
        store = Path(os.environ.get('OLLAMA_MODELS', str(Path.home() / '.ollama' / 'models'))).expanduser()
        # Inspect the nearest existing parent without creating directories in the home folder.
        parent = store
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        free = shutil.disk_usage(parent).free / 1024**3
        print('Model is missing. Setup requires %.1f GiB free on the local model-store volume (%.1f GiB available).' % (budget, free), flush=True)
        print('Download command: ollama pull ' + plan['model'], flush=True)
        if free < budget:
            raise ValueError('Insufficient model-store disk space. Free space before downloading.')
        if not args.yes:
            if not sys.stdin.isatty():
                raise ValueError('Rerun with --yes to authorize the displayed model download.')
            if input('Download these local model weights now? [y/N] ').strip().lower() not in ('y', 'yes'):
                raise ValueError('Download declined. Existing configuration was preserved.')
        environment = provisioning_environment(client.endpoint)
        try:
            subprocess.run([binary, 'pull', plan['model']], env=environment, check=True, timeout=7200)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError('Model download failed or timed out. Rerun setup to retry.') from exc
    client.ready()
    smoke_test(client)
    configuration = {'version': 1, 'model': plan['model'], 'context': plan['context'],
                     'ollama_url': client.endpoint, 'hardware_at_setup': plan['hardware']}
    atomic_json(args.config, configuration)
    print('Local inference passed. Configuration saved to ' + str(Path(args.config).resolve()))
    print('GitHub token: ' + ('configured in this environment.' if os.environ.get('GITHUB_TOKEN') else 'optional for public read-only monitoring; private repositories require read access.'))
    print('Use --config with the saved file when running check, scan or github-watch. Monitoring starts only when you run that command.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prepare local AI without starting the GitHub monitor')
    hardware_options(parser)
    parser.set_defaults(config='.mainline/config.json')
    parser.add_argument('--ollama-url', default='http://127.0.0.1:11434')
    parser.add_argument('--yes', action='store_true', help='Authorize downloading missing model weights')
    parser.add_argument('--offline', action='store_true', help='Verify installed weights without downloads')
    parser.add_argument('--required-free-gb', type=float, help='Explicit disk budget for a custom model')
    args = parser.parse_args(argv)
    try:
        return setup(args)
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError) as exc:
        print('Setup: ' + str(exc), file=sys.stderr)
        return 2
