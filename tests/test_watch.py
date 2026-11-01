import contextlib
import io
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from mainline_agent.cli import main
from mainline_agent.watch import watch_main
from mainline_agent.repository import Repository
from tests.helpers import drift_repo, FRESH_DOC


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.fixture, self.base, self.head = drift_repo()
        self.addCleanup(self.fixture.close)
        storage = tempfile.TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.state = Path(storage.name) / 'baseline.json'
        self.state.write_text(json.dumps({'repository_path': str(self.fixture.path.resolve()), 'head': self.head}))

    def run_watch(self, audit, sleeper=lambda _: None, cycles=2):
        with contextlib.redirect_stdout(io.StringIO()):
            return watch_main(self.fixture.path, self.state, audit, cycles=cycles, sleeper=sleeper)

    def test_unchanged_main_does_not_activate(self):
        calls = []
        self.run_watch(lambda *_: calls.append(1))
        self.assertEqual(calls, [])

    def test_feature_branch_changes_do_not_activate(self):
        self.fixture.git('checkout', '-b', 'feature')
        self.fixture.write('feature.py', 'x = 1')
        self.fixture.commit('Not merged')
        calls = []
        self.run_watch(lambda *_: calls.append(1))
        self.assertEqual(calls, [])

    def test_merge_into_main_activates_once(self):
        calls = []
        def merge(_):
            self.fixture.git('checkout', '-b', 'feature')
            self.fixture.write('feature.py', 'x = 1')
            self.fixture.commit('Feature')
            self.fixture.git('checkout', 'main')
            self.fixture.git('merge', '--no-ff', 'feature', '-m', 'Merge feature')
        self.run_watch(lambda *_: calls.append(Repository(self.fixture.path).head) or 0, sleeper=merge)
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], self.head)

    def merge_stale_rename(self):
        self.fixture.git('checkout', '-b', 'rename')
        self.fixture.write('client.py', 'def retrieve(url, timeout=30):\n    return url\n')
        self.fixture.commit('Rename fetch')
        self.fixture.git('checkout', 'main')
        self.fixture.git('merge', '--no-ff', 'rename', '-m', 'Merge rename')
        return self.fixture.git('rev-parse', 'HEAD')

    def test_direct_commit_does_not_activate(self):
        self.fixture.write('direct.py', 'x = 3')
        self.fixture.commit('Direct commit to main')
        self.run_watch(lambda *_: self.fail('Unexpected audit'))

    def test_failed_audit_preserves_state_and_does_not_repeat(self):
        self.merge_stale_rename()
        calls = []
        self.assertEqual(self.run_watch(lambda *_: calls.append(1) or 2), 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(self.state.read_text())['head'], self.head)

    def test_cli_once_writes_maintainer_warning(self):
        tip = self.merge_stale_rename()
        self.state.unlink()
        out = self.fixture.path / 'report'
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(['watch', '--once', '--path', str(self.fixture.path), '--state', str(self.state),
                         '--no-llm', '--ram-gb', '16', '--unified', '--out', str(out)])
        self.assertEqual(code, 1)
        warning = json.loads((out / 'analysis.log').read_text())
        self.assertTrue(warning['findings'])
        self.assertEqual(warning['head'], tip)
        self.assertTrue(any('fetch' in f['summary'] for f in warning['findings']))

    def test_invalid_interval(self):
        for value in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                watch_main(self.fixture.path, self.state, lambda: 0, interval=value, cycles=1)
