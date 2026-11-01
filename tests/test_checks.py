import unittest

from mainline_agent.checks import signature_findings
from tests.helpers import OLD, NEW, STALE_DOC, FRESH_DOC


class SignatureTests(unittest.TestCase):
    def check(self, old=OLD, new=NEW, doc=STALE_DOC, path="client.py"):
        return signature_findings(path, old, new, [{"path": "README.md", "text": doc}], "base", "head")[0]

    def test_stale_keyword_has_exact_code_and_doc_lines(self):
        finding = self.check()[0]
        self.assertEqual(finding["confidence"], "verified_static")
        self.assertEqual(finding["evidence"][0]["line"], 5)
        self.assertEqual(finding["evidence"][1]["quote"], NEW.splitlines()[0])

    def test_updated_docs_pass(self):
        self.assertEqual(self.check(doc=FRESH_DOC), [])

    def test_new_required_argument_breaks_old_valid_call(self):
        self.assertTrue(self.check(old="def fetch(url): pass", new="def fetch(url, timeout): pass",
                                   doc=STALE_DOC.replace(", retries=3", "")))

    def test_kwargs_and_optional_additions_do_not_break_call(self):
        self.assertEqual(self.check(new="def fetch(url, timeout=30, **kwargs): pass"), [])
        self.assertEqual(self.check(new="def fetch(url, retries=3, timeout=30): pass"), [])

    def test_same_name_different_module_does_not_match(self):
        self.assertEqual(self.check(doc=STALE_DOC.replace("from client", "from other")), [])

    def test_alias_and_src_layout(self):
        doc = STALE_DOC.replace("import fetch", "import fetch as get").replace("fetch('", "get('")
        self.assertTrue(self.check(doc=doc, path="src/client.py"))

    def test_module_alias(self):
        doc = STALE_DOC.replace("from client import fetch", "import client as api").replace("fetch('", "api.fetch('")
        self.assertTrue(self.check(doc=doc))

    def test_dynamic_and_decorated_calls_are_not_claimed_verified(self):
        self.assertEqual(self.check(doc=STALE_DOC.replace("retries=3", "**options")), [])
        self.assertEqual(self.check(new="@wrapper\n" + NEW), [])

    def test_invalid_before_is_not_new_drift(self):
        self.assertEqual(self.check(doc=STALE_DOC.replace("retries=3", "bogus=3")), [])

    def test_syntax_error_is_visible(self):
        results, warnings = signature_findings("client.py", OLD, "def invalid(", [], "b", "h")
        self.assertFalse(results)
        self.assertTrue(warnings)
