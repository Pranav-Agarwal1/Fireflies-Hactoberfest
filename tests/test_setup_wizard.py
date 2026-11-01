import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mainline_agent import setup_wizard as wizard
from mainline_agent.process_env import provisioning_environment


class SetupWizardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.output = io.StringIO()
        for context in (contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.output)):
            context.__enter__()
            self.addCleanup(context.__exit__, None, None, None)

    def test_provisioning_excludes_all_github_credentials_and_forces_local(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'secret', 'GH_TOKEN': 'secret2',
                                    'GH_ENTERPRISE_TOKEN': 'secret3', 'OTHER_API_KEY': 'secret4',
                                    'OLLAMA_HOST': 'https://remote.invalid', 'OLLAMA_NO_CLOUD': '0',
                                    'OLLAMA_MODELS': '/local/models'}):
            env = provisioning_environment(wizard.ENDPOINT)
        self.assertEqual(env['OLLAMA_NO_CLOUD'], '1')
        self.assertEqual(env['OLLAMA_HOST'], wizard.ENDPOINT)
        self.assertEqual(env['OLLAMA_MODELS'], '/local/models')
        self.assertFalse(any('TOKEN' in key or 'KEY' in key for key in env))

    def test_existing_binary_avoids_installation(self):
        with patch.object(wizard, 'find_binary', return_value='/local/ollama'), patch.object(wizard.subprocess, 'run') as run:
            self.assertEqual(wizard.ensure_ollama(), '/local/ollama')
            run.assert_not_called()

    def install(self, consent=True, failure=None, installed='/brew/ollama'):
        with patch.object(wizard, 'find_binary', side_effect=[None, '/brew/brew', installed]), \
             patch.object(wizard.platform, 'system', return_value='Darwin'), \
             patch.object(wizard, 'consent', return_value=consent), \
             patch.object(wizard.subprocess, 'run', side_effect=failure) as run:
            result = wizard.ensure_ollama()
            return result, run.call_args

    def test_formula_install_has_consent_timeout_and_sanitized_environment(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'secret', 'GH_TOKEN': 'secret2'}):
            result, call = self.install()
        self.assertEqual(result, '/brew/ollama')
        self.assertEqual(call.args[0], ['/brew/brew', 'install', 'ollama'])
        self.assertEqual(call.kwargs['timeout'], 1800)
        self.assertNotIn('GH_TOKEN', call.kwargs['env'])
        self.assertNotIn('GITHUB_TOKEN', call.kwargs['env'])

    def test_install_declined_does_not_run(self):
        with patch.object(wizard.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'declined'):
                self.install(consent=False)
            run.assert_not_called()

    def test_failed_install_is_actionable(self):
        for exc in (OSError(), subprocess.CalledProcessError(1, 'brew'), subprocess.TimeoutExpired('brew', 1)):
            with self.subTest(exc=exc), self.assertRaisesRegex(ValueError, 'failed or timed out'):
                self.install(failure=exc)

    def test_installer_missing_binary_is_detected(self):
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.install(installed=None)

    def test_missing_package_manager_or_unsupported_platform(self):
        for system in ('Darwin', 'Linux', 'Windows'):
            with self.subTest(system=system), patch.object(wizard.platform, 'system', return_value=system), \
                 patch.object(wizard, 'find_binary', return_value=None), patch.object(wizard.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'No installer was run'):
                    wizard.ensure_ollama()
                run.assert_not_called()

    def test_reuse_healthy_server_never_spawns_or_stops_it(self):
        with patch.object(wizard, 'server_ready', return_value=True), patch.object(wizard.subprocess, 'Popen') as popen:
            with wizard.local_server('ollama', self.root / 'log', 3):
                pass
            popen.assert_not_called()

    def server(self, readiness, process=None):
        process = process or Mock()
        process.poll.return_value = None
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(wizard, 'server_ready', side_effect=readiness))
        spawn = stack.enter_context(patch.object(wizard.subprocess, 'Popen', return_value=process))
        stack.enter_context(patch.object(wizard.time, 'sleep'))
        return process, spawn

    def test_owned_server_polls_logs_and_cleans_up(self):
        process, spawn = self.server([False, False, True])
        log = self.root / 'logs' / 'ollama.log'
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'secret', 'GH_TOKEN': 'secret2'}):
            with wizard.local_server('/bin/ollama', log, 5):
                self.assertTrue(log.exists())
                process.terminate.assert_not_called()
        env = spawn.call_args.kwargs['env']
        self.assertEqual(env['OLLAMA_HOST'], wizard.ENDPOINT)
        self.assertEqual(env['OLLAMA_NO_CLOUD'], '1')
        self.assertNotIn('GH_TOKEN', env)
        self.assertNotIn('GITHUB_TOKEN', env)
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)

    def test_server_readiness_timeout_stops_owned_process(self):
        process, _ = self.server([False])
        with patch.object(wizard.time, 'monotonic', side_effect=[0, 5]):
            with self.assertRaisesRegex(ValueError, 'timed out'):
                with wizard.local_server('ollama', self.root / 'log', 1):
                    self.fail('must not yield')
        process.terminate.assert_called_once()

    def test_server_exits_early(self):
        process, _ = self.server([False, False])
        process.poll.return_value = 1
        with self.assertRaisesRegex(ValueError, 'exited'):
            with wizard.local_server('ollama', self.root / 'log', 2):
                self.fail('must not yield')
        process.terminate.assert_not_called()

    def test_ctrl_c_stops_owned_server(self):
        process, _ = self.server([False, True])
        with self.assertRaises(KeyboardInterrupt):
            with wizard.local_server('ollama', self.root / 'log', 5):
                raise KeyboardInterrupt()
        process.terminate.assert_called_once()

    def test_stubborn_server_is_killed_with_bounded_wait(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('ollama', 5), 0]
        wizard.stop_server(process)
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)

    def test_invalid_server_inventory_is_not_ready(self):
        client = Mock()
        for result in ({}, {'models': 'invalid'}, {'models': [None]}):
            client.request.return_value = result
            self.assertFalse(wizard.server_ready(client))

    def test_environment_token_has_priority_and_is_not_printed(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'secret'}), patch.object(wizard.subprocess, 'run') as run:
            self.assertEqual(wizard.github_token(), 'secret')
            run.assert_not_called()
        self.assertNotIn('secret', self.output.getvalue())

    def test_gh_token_is_captured_not_persisted_or_printed(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': ''}), \
             patch.object(wizard, 'find_binary', return_value='gh'), \
             patch.object(wizard.subprocess, 'run', return_value=Mock(returncode=0, stdout='hidden-token\n')) as run:
            self.assertEqual(wizard.github_token(), 'hidden-token')
        self.assertEqual(run.call_args.args[0], ['gh', 'auth', 'token', '--hostname', 'github.com'])
        self.assertTrue(run.call_args.kwargs['capture_output'])
        self.assertNotIn('hidden-token', self.output.getvalue())

    def test_failed_gh_falls_back_to_hidden_prompt(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': ''}), patch.object(wizard, 'find_binary', return_value='gh'), \
             patch.object(wizard.subprocess, 'run', side_effect=subprocess.TimeoutExpired('gh', 15)), \
             patch.object(wizard.sys.stdin, 'isatty', return_value=True), \
             patch.object(wizard.getpass, 'getpass', return_value='hidden'):
            self.assertEqual(wizard.github_token(), 'hidden')
        self.assertNotIn('hidden', self.output.getvalue())

    def test_hidden_input_failure_does_not_echo(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': ''}), patch.object(wizard, 'find_binary', return_value=None), \
             patch.object(wizard.sys.stdin, 'isatty', return_value=True), \
             patch.object(wizard.getpass, 'getpass', side_effect=wizard.getpass.GetPassWarning()):
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                wizard.github_token()

    def test_token_environment_restored_after_failure(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'old'}):
            with self.assertRaises(RuntimeError):
                with wizard.token_environment('new'):
                    self.assertEqual(os.environ['GITHUB_TOKEN'], 'new')
                    raise RuntimeError()
            self.assertEqual(os.environ['GITHUB_TOKEN'], 'old')

    def invoke(self, answers, bootstrap_result=0, token='secret', cli_result=0):
        with patch.object(wizard.sys.stdin, 'isatty', return_value=True), \
             patch('builtins.input', side_effect=answers), \
             patch.object(wizard, 'ensure_ollama', return_value='/local/ollama') as ensure, \
             patch.object(wizard, 'local_server', return_value=contextlib.nullcontext()), \
             patch.object(wizard.bootstrap, 'main', return_value=bootstrap_result) as bootstrap, \
             patch.object(wizard, 'github_token', return_value=token) as credentials, \
             patch.object(wizard.cli, 'main', return_value=cli_result) as monitor:
            result = wizard.main(['--directory', str(self.root)])
        return result, monitor, bootstrap, credentials, ensure

    def test_default_is_preview_once_separate_state_and_no_secret_files(self):
        result, monitor, bootstrap, _, _ = self.invoke(['owner/repo', ''])
        self.assertEqual(result, 0)
        args = monitor.call_args.args[0]
        self.assertIn('--once', args)
        self.assertNotIn('--publish', args)
        self.assertEqual(args[args.index('--state') + 1], str(self.root / 'logs-only/owner/repo/state.json'))
        self.assertEqual(bootstrap.call_args.args[0], ['--config', str(self.root / 'config.json'), '--ollama-url', wizard.ENDPOINT])
        self.assertEqual(json.loads((self.root / 'setup.json').read_text()), {'version': 1, 'repository': 'owner/repo'})
        self.assertNotIn('secret', ''.join(p.read_text() for p in self.root.rglob('*') if p.is_file()))

    def test_continuous_monitoring_is_read_only(self):
        result, monitor, _, _, _ = self.invoke(['owner/repo', '2'])
        self.assertEqual(result, 0)
        args = monitor.call_args.args[0]
        self.assertNotIn('--publish', args)
        self.assertNotIn('--once', args)
        self.assertIn(str(self.root / 'logs-only/owner/repo/state.json'), args)
        _, monitor, _, _, _ = self.invoke(['', ''])
        self.assertIn('--once', monitor.call_args.args[0])

    def test_setup_only_skips_credentials_and_monitor(self):
        result, monitor, _, credentials, _ = self.invoke(['owner/repo', '3'])
        self.assertEqual(result, 0)
        monitor.assert_not_called()
        credentials.assert_not_called()

    def test_bootstrap_failure_never_enters_monitor(self):
        result, monitor, _, credentials, _ = self.invoke(['owner/repo', ''], bootstrap_result=2)
        self.assertEqual(result, 2)
        monitor.assert_not_called()
        credentials.assert_not_called()

    def test_continuous_public_monitoring_can_use_no_token(self):
        result, monitor, _, _, _ = self.invoke(['owner/repo', '2'], token='')
        self.assertEqual(result, 0)
        self.assertNotIn('--publish', monitor.call_args.args[0])

    def test_public_preview_can_use_no_token(self):
        result, monitor, _, _, _ = self.invoke(['owner/repo', ''], token='')
        self.assertEqual(result, 0)
        self.assertNotIn('--publish', monitor.call_args.args[0])

    def test_bad_repository_reprompts_before_setup(self):
        result, monitor, _, _, _ = self.invoke(['../../outside', 'owner/repo', ''])
        self.assertEqual(result, 0)
        self.assertIn('owner/repo', monitor.call_args.args[0])

    def test_bad_menu_selection_stops_before_setup(self):
        result, monitor, bootstrap, _, ensure = self.invoke(['owner/repo', 'yes'])
        self.assertEqual(result, 2)
        for mock in (monitor, bootstrap, ensure):
            mock.assert_not_called()

    def test_corrupt_saved_settings_preserved_and_not_used(self):
        path = self.root / 'setup.json'
        for content in ('invalid', '[]', '{"version":1,"repository":"../../outside"}'):
            path.write_text(content)
            with self.assertRaisesRegex(ValueError, 'invalid'):
                wizard.load_settings(path)
            self.assertEqual(path.read_text(), content)

    def test_noninteractive_wizard_has_no_side_effects(self):
        with patch.object(wizard.sys.stdin, 'isatty', return_value=False), patch.object(wizard, 'ensure_ollama') as ensure:
            self.assertEqual(wizard.main(['--directory', str(self.root)]), 2)
            ensure.assert_not_called()
        self.assertFalse((self.root / 'setup.json').exists())

    def test_ctrl_c_at_prompt_returns_130(self):
        with patch.object(wizard.sys.stdin, 'isatty', return_value=True), patch('builtins.input', side_effect=KeyboardInterrupt):
            self.assertEqual(wizard.main(['--directory', str(self.root)]), 130)

    def test_monitor_incomplete_result_is_preserved(self):
        self.assertEqual(self.invoke(['owner/repo', ''], cli_result=2)[0], 2)

    def test_simultaneous_rerun_is_blocked_and_lock_releases(self):
        with wizard.setup_lock(self.root):
            with self.assertRaisesRegex(ValueError, 'Another setup'):
                with wizard.setup_lock(self.root):
                    self.fail('Concurrent setup acquired the same directory')
        with wizard.setup_lock(self.root):
            pass

    def test_real_bootstrap_download_and_validation_flow(self):
        installed = [False]
        requests = []
        def response(client, path, data=None):
            requests.append(path)
            if path == '/api/tags':
                return {'models': [{'name': 'qwen2.5-coder:7b'}] if installed[0] else []}
            if path == '/api/show':
                return {'details': {'quantization_level': 'Q4_K_M'}}
            if path == '/api/chat':
                return {'done': True, 'message': {'content': 'READY'}}
            raise AssertionError(path)
        def download(*args, **kwargs):
            self.assertEqual(args[0], ['/local/ollama', 'pull', 'qwen2.5-coder:7b'])
            self.assertNotIn('GITHUB_TOKEN', kwargs['env'])
            self.assertNotIn('GH_TOKEN', kwargs['env'])
            installed[0] = True
        from mainline_agent.hardware import Hardware
        with patch.object(wizard.sys.stdin, 'isatty', return_value=True), \
             patch('builtins.input', side_effect=['owner/repo', '', 'yes']), \
             patch.object(wizard, 'ensure_ollama', return_value='/local/ollama'), \
             patch.object(wizard, 'local_server', return_value=contextlib.nullcontext()), \
             patch('mainline_agent.cli.detect_hardware', return_value=Hardware('Darwin', 'arm64', 16, True, None)), \
             patch('mainline_agent.llm.Ollama.request', autospec=True, side_effect=response), \
             patch('mainline_agent.bootstrap.shutil.which', return_value='/local/ollama'), \
             patch('mainline_agent.bootstrap.shutil.disk_usage', return_value=Mock(free=100 * 1024**3)), \
             patch('mainline_agent.bootstrap.subprocess.run', side_effect=download) as pull, \
             patch.object(wizard, 'github_token', return_value='memory-only'), \
             patch.object(wizard.cli, 'main', return_value=0) as monitor, \
             patch.dict(os.environ, {'GITHUB_TOKEN': 'secret', 'GH_TOKEN': 'secret2'}):
            self.assertEqual(wizard.main(['--directory', str(self.root)]), 0)
            self.assertEqual(os.environ['GITHUB_TOKEN'], 'secret')
        pull.assert_called_once()
        monitor.assert_called_once()
        config = json.loads((self.root / 'config.json').read_text())
        self.assertEqual(config['model'], 'qwen2.5-coder:7b')
        self.assertIn('/api/show', requests)
        self.assertIn('/api/chat', requests)
        self.assertNotIn('memory-only', ''.join(p.read_text() for p in self.root.rglob('*') if p.is_file()))

    def test_real_bootstrap_download_decline_preserves_config(self):
        from mainline_agent.hardware import Hardware
        config = self.root / 'config.json'
        config.write_text('previous configuration')
        with patch.object(wizard.sys.stdin, 'isatty', return_value=True), \
             patch('builtins.input', side_effect=['owner/repo', '', 'no']), \
             patch.object(wizard, 'ensure_ollama', return_value='/local/ollama'), \
             patch.object(wizard, 'local_server', return_value=contextlib.nullcontext()), \
             patch('mainline_agent.cli.detect_hardware', return_value=Hardware('Darwin', 'arm64', 16, True, None)), \
             patch('mainline_agent.llm.Ollama.request', return_value={'models': []}), \
             patch('mainline_agent.bootstrap.shutil.which', return_value='/local/ollama'), \
             patch('mainline_agent.bootstrap.shutil.disk_usage', return_value=Mock(free=100 * 1024**3)), \
             patch('mainline_agent.bootstrap.subprocess.run') as pull, \
             patch.object(wizard.cli, 'main') as monitor:
            self.assertEqual(wizard.main(['--directory', str(self.root)]), 2)
        self.assertEqual(config.read_text(), 'previous configuration')
        pull.assert_not_called()
        monitor.assert_not_called()


class MacLauncherTests(unittest.TestCase):
    """Run the shell flow with fake Python/Homebrew, never the real installers."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        source = (Path(__file__).resolve().parents[1] / 'setup.command').read_text()
        # Substitute only executable discovery paths so the actual launcher
        # consent, failure handling and sanitized installer invocation run.
        source = source.replace('/opt/homebrew/bin/python3 /usr/local/bin/python3 python3', str(self.bin / 'python3'))
        source = source.replace('export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"',
                                'export PATH="%s:/usr/bin:/bin"' % self.bin)
        self.launcher = self.root / 'setup.command'
        self.launcher.write_text(source)
        self.executable('python3', '''#!/bin/bash
if [ "${1:-}" = '-c' ]; then
    test -f "$(dirname "$0")/installed"
else
    echo "$1" > "$(dirname "$0")/launched"
fi
''')

    def executable(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def launch(self, answer=''):
        env = dict(os.environ, GITHUB_TOKEN='never-inherit', GH_TOKEN='never-inherit-either')
        return subprocess.run(['/bin/bash', str(self.launcher), '--help'], input=answer,
                              capture_output=True, text=True, timeout=10, env=env)

    def test_existing_python_goes_straight_to_wizard(self):
        (self.bin / 'installed').touch()
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.bin / 'launched').read_text().strip(), 'guided_setup.py')

    def test_missing_python_and_brew_has_actionable_error(self):
        result = self.launch()
        self.assertEqual(result.returncode, 2)
        self.assertIn('Python 3.9+ is missing', result.stderr)
        self.assertFalse((self.bin / 'launched').exists())

    def test_declined_python_install_does_not_run_brew(self):
        self.executable('brew', '#!/bin/bash\ntouch "$(dirname "$0")/brew-ran"\n')
        result = self.launch('n\n')
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.bin / 'brew-ran').exists())

    def test_python_install_success_keeps_credentials_out(self):
        self.executable('brew', '''#!/bin/bash
[ "$1 $2" = 'install python' ] || exit 9
[ -z "${GITHUB_TOKEN:-}${GH_TOKEN:-}" ] || exit 10
touch "$(dirname "$0")/installed"
''')
        result = self.launch('yes\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.bin / 'launched').exists())

    def test_python_install_failure_stops_before_wizard(self):
        self.executable('brew', '#!/bin/bash\nexit 1\n')
        result = self.launch('yes\n')
        self.assertEqual(result.returncode, 2)
        self.assertIn('installation failed', result.stderr)
        self.assertFalse((self.bin / 'launched').exists())
