"""Read-only PR and post-merge monitoring; findings stay in local logs."""
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from .github import GitHubSnapshot
from .history import read_json
from .report import atomic_json, write_report


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def complete(report):
    return not (report['errors'] or report['coverage']['skipped'] or
                any('clipped:' in warning for warning in report['warnings']))


class PRMonitor:
    def __init__(self, api, analyse_snapshot, state_path, output, publish=False, analyse_initial=None):
        self.api, self.analyse_snapshot = api, analyse_snapshot
        self.state_path, self.output = Path(state_path), Path(output)
        if publish:
            raise ValueError('Publishing was removed. Use local maintainer logs.')
        self.analyse_initial = analyse_initial
        if self.state_path.exists():
            self.state = read_json(self.state_path)
            if not isinstance(self.state, dict) or self.state.get('repository') != api.repository or not isinstance(self.state.get('prs'), dict):
                raise ValueError('Monitor state belongs to another repository or is invalid.')
        else:
            self.state = {'repository': api.repository, 'since': now(), 'prs': {}}
        self.state.pop('publish', None)
        self.state['mode'] = 'local_logs'

    def save(self):
        atomic_json(self.state_path, self.state)

    def process(self, pr):
        number = pr['number']
        entry = self.state['prs'].setdefault(str(number), {})
        if entry.get('done'):
            if pr['state'] != 'open':
                return
            entry.clear()
        if pr['base']['ref'] != 'main':
            return
        if pr['state'] == 'closed' and not pr.get('merged'):
            entry['done'] = True
            self.save()
            return
        merged = bool(pr.get('merged'))
        head = self.api.request('GET', self.api.root + '/git/ref/heads/main')['object']['sha'] if merged else pr['head']['sha']
        if merged:
            base = entry.get('analysis_base')
            if not base:
                # Recover the actual merge/squash/rebase endpoint's original baseline.
                merge = self.api.request('GET', self.api.root + '/git/commits/' + pr['merge_commit_sha'])
                base = merge['parents'][0]['sha']
                if len(merge['parents']) == 1 and pr.get('commits', 1) > 1:
                    commits = list(self.api.pages(self.api.root + '/pulls/%s/commits' % number))
                    if not commits or len(commits) >= 250:
                        raise ValueError('Cannot establish the complete pre-merge PR baseline.')
                    first = self.api.request('GET', self.api.root + '/git/commits/' + commits[0]['sha'])
                    base = first['parents'][0]['sha']
        else:
            comparison = self.api.request('GET', self.api.root + '/compare/%s...%s' % (pr['base']['sha'], head), params={'per_page': 1})
            base = comparison['merge_base_commit']['sha']
        revision = base + ':' + head
        if not merged and entry.get('revision') == revision:
            return
        paths = set()
        files = list(self.api.pages(self.api.root + '/pulls/%s/files' % number))
        if len(files) >= 3000:
            raise ValueError('PR file list may exceed GitHub limits. Refusing incomplete analysis.')
        for item in files:
            paths.add(item['filename'])
            if item.get('previous_filename'):
                paths.add(item['previous_filename'])
        snapshot = GitHubSnapshot(self.api, base, head, self.output / 'blob-cache', paths)
        report = self.analyse_snapshot(snapshot, base)
        report['branch'] = 'main' if merged else 'pull/%s' % number
        report['pull_request'] = number
        report['discovery']['scope'] = 'Tracked documentation at analysed commit ' + head
        directory = self.output / ('pr-%s' % number) / head
        report['repository'] = self.api.repository
        report['event'] = 'merged_pr' if merged else 'open_pr'
        # Re-read the PR before recording a current result. Changed revisions are retried next poll.
        fresh = self.api.request('GET', self.api.root + '/pulls/%s' % number)
        if fresh['head']['sha'] != pr['head']['sha'] or fresh['base']['sha'] != pr['base']['sha'] or bool(fresh.get('merged')) != merged or fresh['state'] != pr['state']:
            return
        if merged:
            current = self.api.request('GET', self.api.root + '/git/ref/heads/main')['object']['sha']
            if current != head:
                return
            if complete(report):
                entry['done'] = True
        else:
            if complete(report):
                entry['revision'] = revision
                entry['analysis_base'] = base
        write_report(directory, report)
        write_report(self.output, report)
        self.save()

    def initial_audit(self):
        from .initial import current_snapshot, EMPTY
        previous = self.state.get('initial_audit', {})
        if previous.get('complete'):
            return True
        head = self.api.request('GET', self.api.root + '/git/ref/heads/main')['object']['sha']
        if previous.get('head') == head:
            print('Initial audit needs attention. See its local report and use --rescan-initial to retry.', flush=True)
            return False
        snapshot = current_snapshot(self.api, head, self.output / 'blob-cache')
        report = self.analyse_initial(snapshot, EMPTY)
        report['audit_type'] = 'initial_repository_audit'
        report['repository'] = self.api.repository
        report['event'] = 'initial_repository_audit'
        write_report(self.output / 'initial-audit' / head, report)
        write_report(self.output, report)
        self.state['initial_audit'] = {'head': head, 'complete': complete(report), 'findings': len(report['findings'])}
        self.save()
        return complete(report)

    def poll(self):
        if self.analyse_initial is not None and not self.initial_audit():
            raise ValueError('Initial audit is incomplete. Inspect the saved report, resolve its errors/limits, and retry with --rescan-initial.')
        began = now()
        candidates = {pr['number'] for pr in self.api.pages(self.api.root + '/pulls', {'state': 'open', 'base': 'main'})}
        candidates.update(int(number) for number, entry in self.state['prs'].items() if not entry.get('done'))
        for pr in self.api.pages(self.api.root + '/pulls', {'state': 'closed', 'base': 'main', 'sort': 'updated', 'direction': 'desc'}):
            if pr['updated_at'] < self.state['since']:
                break
            if not self.state['prs'].get(str(pr['number']), {}).get('done'):
                candidates.add(pr['number'])
        failures = []
        for number in sorted(candidates):
            try:
                self.process(self.api.request('GET', self.api.root + '/pulls/%s' % number))
            except (ValueError, OSError, KeyError, TypeError) as exc:
                failures.append('PR #%s: %s' % (number, exc))
        if failures:
            raise ValueError('\n'.join(failures))
        self.state['since'] = began
        self.save()

    def run(self, interval=60, once=False, sleeper=time.sleep):
        if not math.isfinite(interval) or interval < 30:
            raise ValueError('GitHub polling interval must be at least 30 seconds.')
        while True:
            try:
                self.poll()
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.output.mkdir(parents=True, exist_ok=True)
                with (self.output / 'maintainer.log').open('a', encoding='utf-8') as stream:
                    stream.write('\n[%s] Monitor error: %s\n' % (now(), exc))
                print('Monitor: ' + str(exc), flush=True)
                if once:
                    return 2
            if once:
                return 0
            sleeper(interval)
