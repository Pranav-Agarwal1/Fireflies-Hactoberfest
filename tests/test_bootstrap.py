import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mainline_agent.bootstrap import main
from mainline_agent.cli import main as cli


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / 'model.json'
        self.args = ['--ram-gb', '16', '--unified', '--config', str(self.config)]
        self.requests = []
        self.installed = True
        def response(client, path, data=None):
            self.requests.append((path, data))
            if path == '/api/tags':
                return {'models': [{'name': 'qwen2.5-coder:7b'}] if self.installed else []}
            if path == '/api/show': return {'details': {'quantization_level': 'Q4_K_M'}}
            if path == '/api/chat': return {'done': True, 'message': {'content': 'READY'}}
            raise AssertionError(path)
        patcher = patch('mainline_agent.llm.Ollama.request', autospec=True, side_effect=response)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_setup(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(self.args + list(extra))

    def test_installed_model_reused_without_download(self):
        with patch('mainline_agent.bootstrap.subprocess.run') as run:
            self.assertEqual(self.run_setup(), 0)
            run.assert_not_called()
        settings = json.loads(self.config.read_text())
        self.assertEqual(settings['model'], 'qwen2.5-coder:7b')
        self.assertEqual(settings['context'], 8192)
        self.assertTrue(any(path == '/api/chat' for path, _ in self.requests))

    def test_download_is_explicit_local_and_has_no_github_token(self):
        self.installed = False
        def download(*args, **kwargs): self.installed = True
        with patch('mainline_agent.bootstrap.shutil.which', return_value='/usr/bin/ollama'), \
             patch('mainline_agent.bootstrap.shutil.disk_usage', return_value=SimpleNamespace(free=100*1024**3)), \
             patch('mainline_agent.bootstrap.subprocess.run', side_effect=download) as run, \
             patch.dict('os.environ', {'OLLAMA_HOST': 'https://remote.invalid', 'GITHUB_TOKEN': 'secret-token'}):
            self.assertEqual(self.run_setup('--yes'), 0)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/ollama', 'pull', 'qwen2.5-coder:7b'])
        self.assertEqual(run.call_args.kwargs['env']['OLLAMA_HOST'], 'http://127.0.0.1:11434')
        self.assertNotIn('GITHUB_TOKEN', run.call_args.kwargs['env'])
        self.assertNotIn('secret-token', self.config.read_text())

    def test_offline_missing_model_never_downloads(self):
        self.installed = False
        with patch('mainline_agent.bootstrap.subprocess.run') as run:
            self.assertEqual(self.run_setup('--offline', '--yes'), 2)
            run.assert_not_called()
        self.assertFalse(self.config.exists())

    def test_insufficient_disk_preserves_existing_configuration(self):
        self.installed = False
        self.config.write_text('old config')
        with patch('mainline_agent.bootstrap.shutil.which', return_value='ollama'), \
             patch('mainline_agent.bootstrap.shutil.disk_usage', return_value=SimpleNamespace(free=1024)), \
             patch('mainline_agent.bootstrap.subprocess.run') as run:
            self.assertEqual(self.run_setup('--yes'), 2)
            run.assert_not_called()
        self.assertEqual(self.config.read_text(), 'old config')

    def test_noninteractive_setup_requires_download_authorization(self):
        self.installed = False
        with patch('mainline_agent.bootstrap.shutil.which', return_value='ollama'), \
             patch('mainline_agent.bootstrap.shutil.disk_usage', return_value=SimpleNamespace(free=100*1024**3)), \
             patch('mainline_agent.bootstrap.sys.stdin.isatty', return_value=False):
            self.assertEqual(self.run_setup(), 2)
        self.assertFalse(self.config.exists())

    def test_missing_server_explains_prerequisite(self):
        with patch('mainline_agent.llm.Ollama.request', side_effect=ValueError('Unavailable')):
            self.assertEqual(self.run_setup(), 2)
        self.assertFalse(self.config.exists())

    def test_failed_inference_does_not_save_ready_config(self):
        def broken(client): raise ValueError('Model could not load')
        with patch('mainline_agent.bootstrap.smoke_test', side_effect=broken):
            self.assertEqual(self.run_setup(), 2)
        self.assertFalse(self.config.exists())

    def test_remote_ai_endpoint_rejected_before_requests(self):
        self.assertEqual(self.run_setup('--ollama-url', 'https://remote.invalid'), 2)
        self.assertEqual(self.requests, [])

    def test_saved_configuration_consumed_by_cli_and_overrides_work(self):
        self.assertEqual(self.run_setup(), 0)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = cli(['doctor', '--config', str(self.config), '--ram-gb', '16', '--unified', '--context', '4096'])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())['context'], 4096)

    def test_invalid_configuration_rejected_cleanly(self):
        self.config.write_text('{"version": 1, "model": "cloud-test", "context": 8192, "ollama_url": "https://evil.invalid"}')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli(['doctor', '--config', str(self.config), '--ram-gb', '16', '--unified']), 2)
