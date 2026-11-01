import json
import unittest
from unittest.mock import patch
from mainline_agent.diagnostics import check_runtime
from mainline_agent.hardware import Hardware, select_model


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.plan = select_model(Hardware('Darwin', 'arm64', 16, True))

    @patch('mainline_agent.diagnostics.Ollama.ready', return_value=None)
    def test_portable_runtime_ready(self, ready):
        with patch.dict('os.environ', {'GITHUB_TOKEN': 'secret-test-value'}):
            result = check_runtime(self.plan)
        self.assertTrue(result['ready'])
        self.assertNotIn('secret-test-value', json.dumps(result))

    @patch('mainline_agent.diagnostics.Ollama.ready', side_effect=ValueError('Local model is missing'))
    def test_missing_weights_give_actionable_setup(self, ready):
        result = check_runtime(self.plan)
        self.assertFalse(result['ready'])
        self.assertIn('ollama pull qwen2.5-coder:7b', json.dumps(result))

    @patch('mainline_agent.diagnostics.Ollama.ready', return_value=None)
    def test_missing_token_only_blocks_publishing(self, ready):
        with patch.dict('os.environ', {}, clear=True):
            self.assertTrue(check_runtime(self.plan)['ready'])
            with self.assertRaisesRegex(ValueError, 'Publishing was removed'):
                check_runtime(self.plan, publish=True)

    @patch('mainline_agent.diagnostics.Ollama.ready', return_value=None)
    def test_check_command_entrypoint(self, ready):
        import contextlib
        import io
        from mainline_agent.cli import main
        output = io.StringIO()
        with patch.dict('os.environ', {'GITHUB_TOKEN': 'synthetic-token'}), contextlib.redirect_stdout(output):
            code = main(['check', '--ram-gb', '16', '--unified'])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())['ready'])
        self.assertNotIn('synthetic-token', output.getvalue())
