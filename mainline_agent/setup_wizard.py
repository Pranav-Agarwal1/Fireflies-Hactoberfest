"""Interactive local-only setup and read-only monitoring."""
import argparse
import contextlib
import getpass
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
import warnings
from pathlib import Path

from . import bootstrap, cli
from .github import GitHub
from .llm import Ollama
from .process_env import provisioning_environment
from .report import atomic_json

ENDPOINT = 'http://127.0.0.1:11434'


def consent(prompt):
    return input(prompt + ' [y/N] ').strip().lower() in ('y', 'yes')


def find_binary(name):
    found = shutil.which(name)
    if found:
        return found
    for prefix in ('/opt/homebrew/bin', '/usr/local/bin'):
        candidate = Path(prefix) / name
        if candidate.is_file() and os.access(str(candidate), os.X_OK):
            return str(candidate)
    return None


def ensure_ollama():
    binary = find_binary('ollama')
    if binary:
        return binary
    brew = find_binary('brew') if platform.system() == 'Darwin' else None
    if not brew:
        raise ValueError('Ollama is missing. Install it from https://ollama.com/download; on macOS install Homebrew from https://brew.sh then rerun. No installer was run.')
    if not consent('Ollama is missing. Allow brew install ollama?'):
        raise ValueError('Ollama installation declined. Rerun when ready.')
    try:
        subprocess.run([brew, 'install', 'ollama'], check=True, timeout=1800,
                       env=provisioning_environment())
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('Homebrew installation failed or timed out. Resolve its error and rerun setup.') from exc
    binary = find_binary('ollama')
    if not binary:
        raise ValueError('Homebrew completed but Ollama was not found. Check your Homebrew installation.')
    return binary


def server_ready(client):
    try:
        models = client.request('/api/tags').get('models')
        return isinstance(models, list) and all(isinstance(model, dict) for model in models)
    except ValueError:
        return False


def stop_server(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@contextlib.contextmanager
def local_server(binary, log_path, timeout):
    client = Ollama({}, ENDPOINT, timeout=min(2, timeout))
    if server_ready(client):
        print('Using the existing loopback Ollama server; local model metadata will be verified.')
        yield
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print('Starting local Ollama with cloud disabled. Log: ' + str(log_path))
    with log_path.open('ab') as log:
        process = subprocess.Popen([binary, 'serve'], stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   env=provisioning_environment(ENDPOINT),
                                   start_new_session=True)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                # A second setup may have won the port race. Reuse only a
                # healthy endpoint; never assume a completed spawn is readiness.
                if server_ready(client):
                    break
                raise ValueError('Ollama exited before readiness. See ' + str(log_path))
            if server_ready(client):
                break
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        else:
            raise ValueError('Ollama readiness timed out. See ' + str(log_path))
        yield
    finally:
        stop_server(process)


def github_token():
    token = os.environ.get('GITHUB_TOKEN', '').strip()
    if token:
        return token
    gh = find_binary('gh')
    if gh:
        try:
            result = subprocess.run([gh, 'auth', 'token', '--hostname', 'github.com'],
                                    capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    # getpass otherwise falls back to echoed input on some redirected streams.
    if not sys.stdin.isatty():
        raise ValueError('Secure token entry requires a terminal. Use gh auth login or set GITHUB_TOKEN and rerun.')
    print('Use an existing GitHub token with repository read access. No write permissions are needed.')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        try:
            return getpass.getpass('GitHub token (hidden; Enter for public read-only access): ').strip()
        except getpass.GetPassWarning as exc:
            raise ValueError('Hidden token entry is unavailable. Use gh auth login or GITHUB_TOKEN.') from exc


@contextlib.contextmanager
def token_environment(token):
    previous = os.environ.get('GITHUB_TOKEN')
    try:
        if token:
            os.environ['GITHUB_TOKEN'] = token
        else:
            os.environ.pop('GITHUB_TOKEN', None)
        yield
    finally:
        if previous is None:
            os.environ.pop('GITHUB_TOKEN', None)
        else:
            os.environ['GITHUB_TOKEN'] = previous


def load_settings(path):
    if not path.exists():
        return {}
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(settings, dict) or settings.get('version') != 1:
            raise ValueError()
        repository = settings.get('repository', '')
        GitHub(repository)  # Validate before using in directory paths or prompts.
        return {'repository': repository}
    except (ValueError, TypeError):
        raise ValueError('Saved setup settings are invalid: %s. Correct or remove this file and rerun.' % path)


@contextlib.contextmanager
def setup_lock(root):
    # OS locks release on crashes; no stale PID file can block a rerun.
    with (root / '.setup.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.write(b'0')
            lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError('Another setup or monitor is using this setup directory. Stop it before rerunning.') from exc
        else:
            import fcntl
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError('Another setup or monitor is using this setup directory. Stop it before rerunning.') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def run(args):
    if not sys.stdin.isatty():
        raise ValueError('Guided setup requires a terminal. Run setup.command in Terminal.')
    root = Path(args.directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with setup_lock(root):
        return guided_run(args, root)


def guided_run(args, root):
    settings_path = root / 'setup.json'
    saved = load_settings(settings_path)
    default = saved.get('repository', '')
    while True:
        repository = input('GitHub OWNER/REPOSITORY%s: ' % (' [' + default + ']' if default else '')).strip() or default
        try:
            GitHub(repository)
            break
        except ValueError:
            print('Enter a repository such as owner/project.')
    print('1. Analyze once and write local logs (default)')
    print('2. Continuous read-only monitoring with local logs')
    print('3. Setup only')
    choice = input('Choose [1]: ').strip() or '1'
    if choice not in ('1', '2', '3'):
        raise ValueError('Choose 1, 2 or 3. No setup was performed.')
    continuous = choice == '2'
    # Only non-secret settings are saved; monitoring never writes to GitHub.
    atomic_json(settings_path, {'version': 1, 'repository': repository})
    binary = ensure_ollama()
    # Finder may supply a minimal PATH; bootstrap looks up the Ollama CLI on PATH.
    os.environ['PATH'] = str(Path(binary).parent) + os.pathsep + os.environ.get('PATH', '')
    config = root / 'config.json'
    with local_server(binary, root / 'logs' / 'ollama.log', args.server_timeout):
        result = bootstrap.main(['--config', str(config), '--ollama-url', ENDPOINT])
        if result:
            return result
        if choice == '3':
            print('Setup complete. Rerun setup.command to preview or monitor. The setup-owned server stops on exit.')
            return 0
        token = github_token()
        target = root / 'logs-only' / repository
        command = ['github-watch', '--repo', repository, '--state', str(target / 'state.json'),
                   '--out', str(target / 'logs'), '--config', str(config)]
        if not continuous:
            command.append('--once')
        print('Continuous read-only monitoring. Press Ctrl+C to stop.' if continuous else 'Analyzing once; results stay in local logs.')
        print('Maintainer log: ' + str(target / 'logs' / 'maintainer.log'))
        with token_environment(token):
            return cli.main(command)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Guided local Mainline Agent setup')
    parser.add_argument('--directory', default=str(Path(__file__).resolve().parents[1] / '.mainline'),
                        help='Non-secret configuration, logs and separated monitor state directory')
    parser.add_argument('--server-timeout', type=float, default=45,
                        help='Maximum seconds to wait for a setup-owned Ollama server')
    args = parser.parse_args(argv)
    if not math.isfinite(args.server_timeout) or args.server_timeout <= 0:
        parser.error('--server-timeout must be positive and finite')
    try:
        return run(args)
    except (KeyboardInterrupt, EOFError):
        print('\nSetup stopped. Rerun setup.command to resume.', file=sys.stderr)
        return 130
    except (ValueError, OSError) as exc:
        print('Setup: ' + str(exc), file=sys.stderr)
        return 2
