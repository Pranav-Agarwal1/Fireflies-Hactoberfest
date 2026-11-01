import contextlib
import io
import json
import socket
import unittest
from unittest.mock import patch

from mainline_agent.cli import main
from mainline_agent.hardware import Hardware, select_model
from mainline_agent.llm import Ollama, NoRedirect
from mainline_agent.repository import Repository, git
from tests.helpers import FixtureRepo, drift_repo


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.fixture, self.base, self.head = drift_repo()
        self.addCleanup(self.fixture.close)

    def scan(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(['scan', '--path', str(self.fixture.path), '--no-llm', '--ram-gb', '16', '--unified',
                         '--out', str(self.fixture.path / 'report')] + list(extra))

    def test_complete_scan_has_no_socket_or_dns_use(self):
        with patch.object(socket, 'create_connection', side_effect=AssertionError('Network attempted')), \
             patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS attempted')):
            self.assertEqual(self.scan(), 1)

    def test_static_checks_do_not_require_model_sized_ram(self):
        self.assertEqual(self.scan('--ram-gb', '4'), 1)

    def test_removed_network_commands(self):
        for command in ('sync', 'history'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main([command, 'o/r'])
            self.assertEqual(caught.exception.code, 2)

    def test_bad_state_shapes_fail_cleanly(self):
        state = self.fixture.path / 'state.json'
        for value in ([], None, {}, {'head': 1}, {'head': '', 'repository_path': str(self.fixture.path)}):
            state.write_text(json.dumps(value))
            with self.subTest(value=value):
                self.assertEqual(self.scan('--state', str(state)), 2)

    def test_bad_history_shapes_fail_cleanly(self):
        history = self.fixture.path / 'history.json'
        for text in ('bad json', 'null', '[]', '{"items": null}'):
            history.write_text(text)
            self.assertEqual(self.scan('--history-json', str(history)), 2)

    def test_git_cannot_fetch_missing_objects(self):
        with patch('mainline_agent.repository.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b''
            git(self.fixture.path, 'rev-parse', 'HEAD')
        env = run.call_args.kwargs['env']
        self.assertEqual(env['GIT_ALLOW_PROTOCOL'], '')
        self.assertEqual(env['GIT_NO_LAZY_FETCH'], '1')
        self.assertEqual(env['GIT_NO_REPLACE_OBJECTS'], '1')

    def test_one_commit_repository_requires_baseline_history(self):
        repo = FixtureRepo()
        self.addCleanup(repo.close)
        repo.write('README.md', 'hello')
        repo.commit('first')
        with self.assertRaisesRegex(ValueError, 'no previous'):
            Repository(repo.path).baseline()

    def test_invalid_sha_is_rejected(self):
        repo = Repository(self.fixture.path)
        for value in ('HEAD~1', '--all', '', 'f'*40, 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                repo.baseline(value)

    def test_unicode_and_space_paths(self):
        self.fixture.write('docs/guide é.md', 'Documentation')
        self.fixture.commit('Unicode docs')
        self.assertIn('docs/guide é.md', Repository(self.fixture.path).files(self.fixture.git('rev-parse', 'HEAD')))

    def test_binary_source_is_unverified(self):
        self.fixture.write('binary.py', 'x\x00y')
        self.fixture.commit('Binary')
        self.assertEqual(self.scan(), 2)

    def test_invalid_python_is_unverified(self):
        self.fixture.write('client.py', 'def broken(')
        self.fixture.commit('Syntax error')
        self.assertEqual(self.scan(), 2)

    def test_no_changes_does_not_read_documents(self):
        with patch('mainline_agent.repository.Repository.read', side_effect=AssertionError('Unexpected blob read')):
            self.assertEqual(self.scan('--base', self.head), 0)

    def test_localhost_is_normalized_and_remote_addresses_rejected(self):
        plan = select_model(Hardware('Darwin', 'arm64', 16, True))
        self.assertEqual(Ollama(plan, 'http://localhost:11434').endpoint, 'http://127.0.0.1:11434')
        for endpoint in ('http://8.8.8.8', 'https://127.0.0.1', 'http://127.0.0.1@evil.org', 'http://localhost:11434?url=evil'):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                Ollama(plan, endpoint)

    def test_redirects_never_leave_localhost(self):
        with self.assertRaisesRegex(ValueError, 'redirects'):
            NoRedirect().redirect_request(None, None, 302, 'Found', {}, 'https://example.org')
