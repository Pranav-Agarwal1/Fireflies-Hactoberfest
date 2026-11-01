import contextlib
import io
import json
import unittest
from mainline_agent.prose import default_drift
from mainline_agent.cli import main
from tests.helpers import FixtureRepo


class ProseTests(unittest.TestCase):
    old = 'def connect(timeout=30):\n    return timeout\n'
    new = old.replace('=30', '=60')

    def check(self, text, old=None, new=None):
        return default_drift('client.py', old or self.old, new or self.new,
                             [{'path': 'docs/api.md', 'text': text}], 'base', 'head')

    def test_exact_sentence_and_code_locations(self):
        text = '# connect\n\nUse `connect` to open a connection. The `timeout` defaults to 30 seconds.\n'
        result = self.check(text)[0]
        evidence = result['evidence'][0]
        self.assertEqual(evidence['quote'].strip(), 'The `timeout` defaults to 30 seconds.')
        self.assertEqual(evidence['line'], 3)
        self.assertEqual(result['evidence'][2]['quote'], self.new.splitlines()[0])

    def test_configuration_table_default_header(self):
        text = '# connect\n\n| Parameter | Default |\n| --- | --- |\n| timeout | 30 |\n'
        evidence = self.check(text)[0]['evidence'][0]
        self.assertEqual(evidence['line'], 5)
        self.assertEqual(evidence['quote'], '| timeout | 30 |')

    def test_wrapped_prose_keeps_original_lines(self):
        text = '# connect\n\nThe `timeout` defaults\nto 30 seconds.\n'
        evidence = self.check(text)[0]['evidence'][0]
        self.assertEqual((evidence['line'], evidence['end_line']), (3, 4))
        self.assertIn('\n', evidence['quote'])

    def test_updated_docs_and_migration_descriptions_do_not_warn(self):
        for text in ('connect timeout defaults to 60 seconds.', 'connect timeout previously defaulted to 30 seconds.',
                     'connect timeout default changed from 30 to 60.', 'connect accepts a timeout of 30 in this example.'):
            with self.subTest(text=text):
                self.assertEqual(self.check(text), [])

    def test_unrelated_common_parameter_is_not_associated(self):
        self.assertEqual(self.check('# disconnect\nThe timeout defaults to 30 seconds.'), [])

    def test_numeric_substring_does_not_match(self):
        self.assertEqual(self.check('connect timeout defaults to 300 seconds.'), [])

    def test_argparse_flag_default(self):
        old = "parser.add_argument('--retries', default=3)\n"
        new = old.replace('=3', '=5')
        self.assertTrue(self.check('The --retries flag defaults to 3.', old, new))

    def test_constant_default(self):
        self.assertTrue(self.check('The DEFAULT_TIMEOUT setting defaults to 30 seconds.',
                                   'DEFAULT_TIMEOUT = 30\n', 'DEFAULT_TIMEOUT = 60\n'))

    def test_unchanged_code_or_corrected_docs_do_not_warn(self):
        self.assertEqual(self.check('connect timeout defaults to 30.', self.old, self.old), [])
        self.assertEqual(self.check('connect timeout defaults to 60.'), [])

    def test_historical_replay_cannot_see_later_doc_fix(self):
        fixture = FixtureRepo()
        self.addCleanup(fixture.close)
        fixture.write('client.py', self.old)
        fixture.write('README.md', '# connect\nThe timeout defaults to 30 seconds.\n')
        base = fixture.commit('Document old default')
        fixture.write('client.py', self.new)
        drift = fixture.commit('Change timeout default')
        fixture.write('README.md', '# connect\nThe timeout defaults to 60 seconds.\n')
        fixture.commit('Correct docs later')
        out = fixture.path / 'reports'
        with contextlib.redirect_stdout(io.StringIO()):
            result = main(['scan', '--path', str(fixture.path), '--at-main', drift, '--base', base,
                           '--no-llm', '--out', str(out)])
        self.assertEqual(result, 1)
        report = json.loads((out / 'analysis.log').read_text())
        self.assertEqual(report['head'], drift)
        self.assertIn('30', report['findings'][0]['evidence'][0]['quote'])
        self.assertIn('-def connect(timeout=30)', report['findings'][0]['code_changes'][0]['patch'])
        self.assertIn('+def connect(timeout=60)', report['findings'][0]['code_changes'][0]['patch'])

    def test_gaps_are_opt_in(self):
        fixture = FixtureRepo()
        self.addCleanup(fixture.close)
        fixture.write('client.py', 'x = 1\n')
        fixture.commit('Initial')
        fixture.write('client.py', 'x = 1\ndef new_feature():\n    return 42\n')
        fixture.commit('Add public function')
        with contextlib.redirect_stdout(io.StringIO()):
            result = main(['scan', '--path', str(fixture.path), '--no-llm', '--out', str(fixture.path / 'reports')])
        self.assertEqual(result, 0)

    def test_distinct_default_sentences_on_same_line_are_preserved(self):
        old = 'def connect(timeout=30, retries=2): pass\n'
        new = 'def connect(timeout=60, retries=4): pass\n'
        text = 'connect timeout defaults to 30 seconds. Its retries default is 2.'
        self.assertEqual(len(self.check(text, old, new)), 2)

    def test_override_instruction_does_not_assert_current_default(self):
        self.assertEqual(self.check('To override the default, pass connect timeout=30 for this example.'), [])

    def test_bounded_source_windows_keep_all_text_and_lines(self):
        from mainline_agent.discovery import text_windows, is_code, ignored
        text = 'first line\n' + 'x'*1300 + '\nlast line\n'
        windows = list(text_windows(text, 650))
        self.assertEqual(''.join(part for _, part in windows), text)
        self.assertTrue(all(len(part) <= 650 for _, part in windows))
        for extension in ('cs', 'shader', 'hlsl', 'cginc', 'prefab', 'asset', 'unity'):
            self.assertTrue(is_code('Assets/Game.' + extension))
        self.assertTrue(ignored('Assets/TextMesh Pro/Shaders/library.shader'))
