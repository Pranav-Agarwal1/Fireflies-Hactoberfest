"""Watch the local main ref. Only a first-parent merge triggers an audit."""
import math
import time
from pathlib import Path

from .history import read_json
from .report import atomic_json
from .repository import Repository, git


def watch_main(path, state_path, audit, interval=2, cycles=None, sleeper=time.sleep):
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError('Watch interval must be positive and finite.')
    state_path = Path(state_path)
    repo = Repository(path)
    if state_path.exists():
        state = read_json(state_path)
        if not isinstance(state, dict) or state.get('repository_path') != str(repo.path):
            raise ValueError('Watcher baseline belongs to another repository or is invalid.')
        if not isinstance(state.get('head'), str):
            raise ValueError('Invalid watcher baseline SHA.')
        baseline = repo.baseline(state['head'])
    else:
        # On first launch, audit a merge at the tip, otherwise arm at today's main.
        parents = git(path, 'rev-list', '--parents', '-n', '1', repo.head).split()[1:]
        baseline = parents[0] if len(parents) > 1 else repo.head
        atomic_json(state_path, {'repository_path': str(repo.path), 'head': baseline})
    observed = baseline
    count, result = 0, 0
    while cycles is None or count < cycles:
        head = git(path, 'rev-parse', '--verify', 'refs/heads/main^{commit}')
        if head != observed:
            repo = Repository(path)
            repo.baseline(observed)  # Reject force-pushes before advancing state.
            merges = git(path, 'rev-list', '--first-parent', '--min-parents=2',
                         '--max-count=1', observed + '..' + repo.head)
            if merges:
                print('Merge reached local main: ' + merges, flush=True)
                result = audit(repo, baseline)
                if result in (0, 1):
                    baseline = repo.head
            observed = repo.head
        count += 1
        if cycles is None or count < cycles:
            sleeper(interval)
    return result
