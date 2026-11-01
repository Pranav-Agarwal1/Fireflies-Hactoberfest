"""Check portable runtime prerequisites without installing software or exposing secrets."""
import os
import shutil
import sys
from .llm import Ollama


def check_runtime(plan, endpoint='http://127.0.0.1:11434', publish=False):
    if publish:
        raise ValueError('Publishing was removed. Use local maintainer logs.')
    checks = [{'name': 'Python 3.9+', 'ok': sys.version_info >= (3, 9),
               'detail': sys.version.split()[0]},
              {'name': 'Python packages', 'ok': True, 'detail': 'Standard library only. No pip runtime dependencies.'}]
    try:
        Ollama(plan, endpoint).ready()
        checks.append({'name': 'Local AI', 'ok': True, 'detail': 'Local server and selected weights available.'})
    except ValueError as exc:
        checks.append({'name': 'Local AI', 'ok': False,
                       'detail': str(exc), 'setup': 'Install/start Ollama, then run ollama pull ' + plan['model']})
    token_present = bool(os.environ.get('GITHUB_TOKEN'))
    checks.append({'name': 'GitHub token', 'ok': True,
                   'detail': 'Configured.' if token_present else 'Optional for public read-only monitoring; private repositories need read access.'})
    return {'ready': all(check['ok'] for check in checks), 'model_plan': plan, 'checks': checks,
            'git': 'available' if shutil.which('git') else 'optional for GitHub mode; required for local repository scans',
            'note': 'Network access to GitHub and token permissions are checked during monitoring.'}
