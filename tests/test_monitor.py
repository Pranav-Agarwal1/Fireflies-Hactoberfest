import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mainline_agent.engine import analyse
from mainline_agent.monitor import PRMonitor
from mainline_agent.github import GitHub, GitHubSnapshot
from mainline_agent.hardware import Hardware, select_model
from mainline_agent.repository import Repository
from tests.helpers import drift_repo, FRESH_DOC


class FakeAPI:
    repository = 'org/project'
    root = '/repos/org/project'
    token = 'test-only-token'

    def __init__(self, base, head):
        self.base, self.head = base, head
        self.pr = {'number': 7, 'state': 'open', 'merged': False, 'updated_at': '2099-01-01T00:00:00Z',
                   'base': {'ref': 'main', 'sha': base}, 'head': {'sha': head, 'repo': {'full_name': self.repository}},
                   'merge_commit_sha': head}
        self.writes = []

    def pages(self, path, params=None):
        if path.endswith('/files'):
            return [{'filename': 'client.py'}]
        if path.endswith('/pulls'):
            return [copy.deepcopy(self.pr)] if self.pr['state'] == params['state'] else []
        raise AssertionError('Unexpected GitHub collection: ' + path)

    def request(self, method, path, data=None, params=None):
        if method != 'GET' or data is not None:
            self.writes.append((method, path, data))
            raise AssertionError('Remote mutations are prohibited')
        if path.endswith('/pulls/7'): return copy.deepcopy(self.pr)
        if '/compare/' in path: return {'merge_base_commit': {'sha': self.base}}
        if '/git/ref/' in path: return {'object': {'sha': self.head}}
        if '/git/commits/' in path: return {'parents': [{'sha': self.base}]}
        raise AssertionError('Unexpected GitHub read: ' + path)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.fixture, self.base, self.head = drift_repo()
        self.addCleanup(self.fixture.close)
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.directory = Path(self.storage.name)
        self.api = FakeAPI(self.base, self.head)
        self.plan = select_model(Hardware('Darwin', 'arm64', 16, True))
        self.report = analyse(Repository(self.fixture.path), self.base, self.plan)
        self.snapshot = patch('mainline_agent.monitor.GitHubSnapshot', return_value=Repository(self.fixture.path)).start()
        self.addCleanup(patch.stopall)
        self.monitor = PRMonitor(self.api, lambda *_: copy.deepcopy(self.report), self.directory / 'state.json', self.directory / 'reports')

    def log(self):
        path = self.directory / 'reports' / 'maintainer.log'
        return path.read_text() if path.exists() else ''

    def test_open_pr_logs_quoted_docs_and_code_without_writes(self):
        self.monitor.poll()
        for text in ('retries', 'client.py', 'README.md', self.head, 'Pull request: #7'):
            self.assertIn(text, self.log())
        self.assertEqual(self.api.writes, [])

    def test_unchanged_pr_does_not_repeat_analysis_or_log(self):
        self.monitor.poll()
        first = self.log()
        self.monitor.poll()
        self.assertEqual(self.snapshot.call_count, 1)
        self.assertEqual(first, self.log())

    def test_new_commit_appends_local_log(self):
        self.monitor.poll()
        first = self.log()
        self.api.pr['head']['sha'] = 'c' * 40
        self.report['head'] = 'c' * 40
        self.monitor.poll()
        self.assertTrue(self.log().startswith(first))
        self.assertIn('c' * 40, self.log())
        self.assertEqual(self.api.writes, [])

    def test_merge_rechecks_current_main_once_and_logs_drift(self):
        self.monitor.poll()
        self.api.pr.update(state='closed', merged=True)
        self.monitor.poll()
        first = self.log()
        self.monitor.poll()
        self.assertEqual(self.snapshot.call_count, 2)
        self.assertIn('merged_pr', first)
        self.assertEqual(first, self.log())
        self.assertEqual(self.api.writes, [])

    def test_fixed_before_merge_logs_clean_result(self):
        self.monitor.poll()
        self.api.pr.update(state='closed', merged=True)
        self.report['findings'] = []
        self.monitor.poll()
        self.assertIn('No discrepancies established', self.log())
        self.assertEqual(self.api.writes, [])

    def test_closed_without_merge_is_marked_done(self):
        self.monitor.poll()
        self.api.pr['state'] = 'closed'
        self.monitor.poll()
        self.assertTrue(self.monitor.state['prs']['7']['done'])
        self.assertEqual(self.api.writes, [])

    def test_pr_changes_during_analysis_not_recorded_as_current(self):
        def changing(*args):
            self.api.pr['head']['sha'] = 'd' * 40
            return copy.deepcopy(self.report)
        self.monitor.analyse_snapshot = changing
        self.monitor.poll()
        self.assertEqual(self.log(), '')
        self.assertNotIn('revision', self.monitor.state['prs']['7'])

    def test_incomplete_scan_logs_errors_without_advancing(self):
        self.report['errors'] = [{'message': 'Model unavailable'}]
        self.monitor.poll()
        self.assertIn('Model unavailable', self.log())
        self.assertNotIn('revision', self.monitor.state['prs']['7'])
        self.api.pr.update(state='closed', merged=True)
        self.monitor.poll()
        self.assertNotIn('done', self.monitor.state['prs']['7'])

    def test_old_publishing_opt_in_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Publishing was removed'):
            PRMonitor(self.api, lambda *_: None, self.directory / 'old.json', self.directory, True)

    def test_pr_merged_between_polls_is_discovered(self):
        self.api.pr.update(state='closed', merged=True)
        self.monitor.poll()
        self.assertIn('merged_pr', self.log())
        self.assertEqual(self.api.writes, [])

    def test_fixed_open_pr_logs_resolved_result(self):
        self.monitor.poll()
        self.api.pr['head']['sha'] = 'e' * 40
        self.report['head'] = 'e' * 40
        self.report['findings'] = []
        self.monitor.poll()
        self.assertIn('No discrepancies established', self.log())
        self.assertEqual(self.api.writes, [])

    def test_reopened_pr_is_monitored_again(self):
        self.monitor.poll()
        self.api.pr['state'] = 'closed'
        self.monitor.poll()
        self.api.pr['state'] = 'open'
        self.monitor.poll()
        self.assertEqual(self.snapshot.call_count, 2)

    def apply_documentation_commit(self):
        self.fixture.write('README.md', FRESH_DOC)
        self.fixture.git('add', 'README.md')
        self.fixture.git('commit', '-m', 'Fix docs')
        head = self.fixture.git('rev-parse', 'HEAD')
        self.api.pr['head']['sha'] = head
        self.api.head = head
        self.snapshot.side_effect = lambda *_: Repository(self.fixture.path)
        self.monitor.analyse_snapshot = lambda repo, base: analyse(repo, base, self.plan)

    def test_real_doc_fix_after_first_log_clears_later_findings(self):
        self.monitor.poll()
        self.apply_documentation_commit()
        self.monitor.poll()
        self.assertIn('No discrepancies established', self.log())
        self.api.pr.update(state='closed', merged=True)
        self.monitor.poll()
        self.assertEqual(self.api.writes, [])

    def test_doc_commit_and_merge_between_polls_logs_current_docs(self):
        self.monitor.poll()
        self.apply_documentation_commit()
        self.api.pr.update(state='closed', merged=True)
        self.monitor.poll()
        self.assertIn('No discrepancies established', self.log())
        self.assertEqual(self.api.writes, [])

    def test_main_changes_during_analysis_defers_completion(self):
        self.api.pr.update(state='closed', merged=True)
        def changing(*args):
            self.api.head = 'f' * 40
            return copy.deepcopy(self.report)
        self.monitor.analyse_snapshot = changing
        self.monitor.poll()
        self.assertFalse(self.monitor.state['prs']['7'].get('done',False))
        self.assertEqual(self.log(), '')

    def test_other_base_is_ignored(self):
        self.api.pr['base']['ref'] = 'develop'
        self.monitor.poll()
        self.assertEqual(self.snapshot.call_count, 0)
        self.assertEqual(self.api.writes, [])

    def test_old_state_migrates_to_local_logs(self):
        from mainline_agent.report import atomic_json
        path = self.directory / 'legacy.json'
        atomic_json(path, {'repository':self.api.repository,'prs':{},'since':'2000','publish':True})
        monitor = PRMonitor(self.api, lambda *_: self.report, path, self.directory)
        self.assertEqual(monitor.state['mode'], 'local_logs')
        self.assertNotIn('publish', monitor.state)

    def test_monitor_errors_are_written_to_local_log(self):
        with patch.object(self.monitor,'poll',side_effect=ValueError('synthetic failure')):
            self.assertEqual(self.monitor.run(once=True),2)
        self.assertIn('synthetic failure',self.log())


class GitHubTests(unittest.TestCase):
    def test_every_remote_mutation_is_rejected_before_transport(self):
        api = GitHub('o/r', token='secret')
        with patch.object(api.opener, 'open') as transport:
            for method in ('POST', 'PATCH', 'PUT', 'DELETE'):
                with self.subTest(method=method), self.assertRaisesRegex(ValueError, 'read-only'):
                    api.request(method, '/repos/o/r/issues', {'body':'must not publish'})
            with self.assertRaisesRegex(ValueError, 'read-only'):
                api.request('GET', '/repos/o/r/issues', {'body':'no GET bodies'})
            transport.assert_not_called()

    def test_rejects_repository_injection_and_non_repo_requests(self):
        for repo in ('https://github.com/o/r', '../r', 'o/r?token=x'):
            with self.assertRaises(ValueError): GitHub(repo)
        with self.assertRaises(ValueError): GitHub('o/r').request('GET', '/evil')

    def test_pagination_collects_every_page(self):
        api = GitHub('o/r')
        with patch.object(api, 'request', side_effect=[list(range(100)), [100]]) as request:
            self.assertEqual(len(list(api.pages('/repos/o/r/pulls'))), 101)
        self.assertEqual(request.call_args.kwargs['params']['page'], 2)

    def test_truncated_source_tree_is_rejected(self):
        api = GitHub('o/r')
        with tempfile.TemporaryDirectory() as directory, patch.object(api, 'request', side_effect=[{'tree': {'sha': 'a'*40}}, {'truncated': True}]):
            with self.assertRaisesRegex(ValueError, 'truncated'):
                GitHubSnapshot(api, 'a'*40, 'b'*40, directory)

    def test_real_snapshot_adapter_detects_prose_drift_and_reuses_blob_cache(self):
        import base64
        import hashlib
        from mainline_agent.github import GitHubSnapshot
        old = b'def connect(timeout=30): pass\n'
        new = b'def connect(timeout=60): pass\n'
        doc = b'# connect\nThe timeout defaults to 30 seconds.\n'
        def digest(data):
            return hashlib.sha1(('blob %s\0' % len(data)).encode() + data).hexdigest()
        blobs = {digest(data): data for data in (old, new, doc)}
        commits = {'a'*40: old, 'b'*40: new}
        api = GitHub('o/r')
        calls = []
        def request(method, path, data=None, params=None):
            calls.append(path)
            if '/git/commits/' in path: return {'tree': {'sha': path.rsplit('/', 1)[-1]}}
            if '/git/trees/' in path:
                code = commits[path.rsplit('/', 1)[-1]]
                return {'tree': [{'path': name, 'type': 'blob', 'mode': '100644', 'size': len(body), 'sha': digest(body)}
                                 for name, body in [('client.py', code), ('README.md', doc)]]}
            blob = path.rsplit('/', 1)[-1]
            return {'encoding': 'base64', 'content': base64.b64encode(blobs[blob]).decode()}
        with tempfile.TemporaryDirectory() as directory, patch.object(api, 'request', side_effect=request):
            snapshot = GitHubSnapshot(api, 'a'*40, 'b'*40, directory, {'client.py'})
            report = analyse(snapshot, 'a'*40, {'model': 'disabled'})
            self.assertEqual(len(report['findings']), 1)
            self.assertIn('30 seconds', report['findings'][0]['evidence'][0]['quote'])
            self.assertEqual(len([p for p in calls if '/git/blobs/' in p]), 3)
            snapshot.read('b'*40, 'README.md')
            self.assertEqual(len([p for p in calls if '/git/blobs/' in p]), 3)


class InitialAuditTests(unittest.TestCase):
    setUp = MonitorTests.setUp
    log = MonitorTests.log

    def enable_initial(self):
        from mainline_agent.initial import InitialSnapshot
        patcher = patch('mainline_agent.initial.current_snapshot', return_value=InitialSnapshot(Repository(self.fixture.path)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.initial = lambda snapshot, base: analyse(snapshot, base, self.plan, initial=True)
        self.monitor.analyse_initial = self.initial

    def test_first_run_audits_repository_once_then_monitors_prs(self):
        self.enable_initial()
        with patch.object(self.monitor, 'analyse_initial', wraps=self.initial) as initial:
            self.monitor.poll()
            self.monitor.poll()
        self.assertEqual(initial.call_count, 1)
        self.assertTrue(self.monitor.state['initial_audit']['complete'])
        self.assertEqual(self.api.writes, [])
        self.assertIn('initial_repository_audit', self.log())

    def test_incomplete_initial_is_not_recorded_as_complete(self):
        self.enable_initial()
        self.monitor.analyse_initial = lambda *_: dict(copy.deepcopy(self.report), errors=[{'message': 'Incomplete'}])
        with self.assertRaisesRegex(ValueError, 'Initial audit is incomplete'):
            self.monitor.poll()
        with self.assertRaisesRegex(ValueError, 'Initial audit is incomplete'):
            self.monitor.poll()
        self.assertFalse(self.monitor.state['initial_audit']['complete'])
        self.assertEqual(self.api.writes, [])
        self.assertIn('Incomplete', self.log())

    def test_initial_scan_sees_current_defaults_without_prior_commits(self):
        from mainline_agent.initial import InitialSnapshot, EMPTY
        self.fixture.write('client.py', 'def connect(timeout=60): pass\n')
        self.fixture.write('README.md', '# connect\nThe timeout defaults to 30 seconds.\n')
        self.fixture.commit('Current snapshot with stale prose')
        repository = InitialSnapshot(Repository(self.fixture.path))
        report = analyse(repository, EMPTY, self.plan, initial=True)
        self.assertTrue(any('current declared default' in f['summary'] for f in report['findings']))
        self.assertEqual(report['audit_type'], 'initial_repository_audit')
        self.assertTrue(all(not f['code_changes'] for f in report['findings']))

    def test_completed_initial_audit_is_reused_after_restart(self):
        self.enable_initial()
        self.monitor.poll()
        def unexpected(*args):
            self.fail('Full audit was repeated after restart')
        restarted = PRMonitor(self.api, lambda *_: copy.deepcopy(self.report), self.directory / 'state.json',
                              self.directory / 'reports', False, analyse_initial=unexpected)
        restarted.poll()
        self.assertEqual(self.api.writes, [])

    def test_documentation_first_initial_indexes_csharp_and_reviews_documents(self):
        from mainline_agent.initial import audit_repository, InitialSnapshot, EMPTY
        self.fixture.write('PlayerHealth.cs', 'public class PlayerHealth { public int health = 90; }')
        self.fixture.write('README.md', '# PlayerHealth\nPlayerHealth has health 100.\n')
        self.fixture.commit('Unity current snapshot')
        class Reviewer:
            def __init__(self): self.packets = []
            def review(self, evidence):
                self.packets.append(evidence)
                return []
        reviewer = Reviewer()
        report = audit_repository(InitialSnapshot(Repository(self.fixture.path)), EMPTY, self.plan, reviewer)
        self.assertTrue(reviewer.packets)
        self.assertTrue(any(e.get('path') == 'PlayerHealth.cs' for e in reviewer.packets[0]))
        self.assertEqual(report['coverage']['code_files_read'], 2)
        self.assertFalse(report['coverage']['skipped'])

    def test_limited_initial_audit_reports_partial_coverage_and_progress(self):
        from mainline_agent.initial import audit_repository, InitialSnapshot, EMPTY
        self.fixture.write('README.md', 'fetch documentation. ' * 100)
        self.fixture.commit('Multiple sections')
        class Reviewer:
            def review(self, evidence): return []
        checkpoints = []
        report = audit_repository(InitialSnapshot(Repository(self.fixture.path)), EMPTY, self.plan, Reviewer(),
                                  max_sections=1, progress=lambda report: checkpoints.append(len(report['findings'])))
        self.assertTrue(checkpoints)
        self.assertTrue(report['coverage']['partial'])
        self.assertEqual(report['coverage']['documentation_sections_reviewed'], 1)
        self.assertTrue(report['coverage']['skipped'])
        self.assertIn('retrieval_seconds', report['timings'])
