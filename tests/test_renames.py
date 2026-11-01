import contextlib
import io
import json
import unittest
from mainline_agent.cli import main
from mainline_agent.checks import signature_findings
from tests.helpers import OLD, STALE_DOC, drift_repo


class RenameTests(unittest.TestCase):
    def check(self, new):
        return signature_findings('client.py', OLD, new, [{'path': 'README.md', 'text': STALE_DOC}], 'base', 'head')[0]

    def test_documented_removed_function_warns(self):
        result = self.check(OLD.replace('fetch', 'retrieve'))
        self.assertEqual(result[0]['replacement_candidates'], ['retrieve'])
        self.assertEqual(result[0]['confidence'], 'possible_static')

    def test_backward_compatible_alias_does_not_warn(self):
        self.assertEqual(self.check(OLD.replace('fetch', 'retrieve') + '\nfetch = retrieve\n'), [])

    def test_reexport_does_not_warn_removed(self):
        self.assertEqual(self.check('from elsewhere import fetch\n'), [])

    def test_decorated_function_not_mistaken_for_removal(self):
        self.assertEqual(self.check('@wrapper\n' + OLD), [])

    def test_new_function_missing_name_in_docs_warns_for_review(self):
        fixture, base, _ = drift_repo()
        self.addCleanup(fixture.close)
        fixture.write('client.py', OLD + '\ndef list_users():\n    return []\n')
        fixture.commit('Add undocumented public function')
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(['scan', '--include-gaps', '--path', str(fixture.path), '--no-llm', '--ram-gb', '16', '--unified',
                         '--out', str(fixture.path / 'report')])
        self.assertEqual(code, 1)
        report = json.loads((fixture.path / 'report/analysis.log').read_text())
        self.assertTrue(any(f['kind'] == 'documentation_gap' and f['confidence'] == 'needs_review' for f in report['findings']))
