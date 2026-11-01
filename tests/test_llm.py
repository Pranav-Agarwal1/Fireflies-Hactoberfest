import json
import unittest
from unittest.mock import patch

from mainline_agent.hardware import Hardware, select_model
from mainline_agent.llm import Ollama, validate_response


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [{"id": "code", "type": "code_head", "text": "def fetch(url, timeout=30):", "line": 10},
                         {"id": "doc", "type": "doc_head", "text": "fetch(url, retries=3)", "line": 5},
                         {"id": "past", "type": "history", "text": "We decided to keep fetch.", "url": "https://github.com/o/r/issues/1"}]
        self.payload = {"findings": [{"kind": "documentation_drift", "summary": "Keyword changed",
                                    "evidence": [{"id": "code", "quote": "timeout=30"}, {"id": "doc", "quote": "retries=3"}]}]}
        verifier = patch('mainline_agent.llm.Ollama.verify_contradiction', return_value=True)
        verifier.start()
        self.addCleanup(verifier.stop)
        self.plan = select_model(Hardware("Darwin", "arm64", 16, True))

    def test_valid_model_result_stays_advisory(self):
        self.assertEqual(validate_response(self.payload, self.evidence)[0]["confidence"], "model_suggested")

    def test_null_optional_decision_is_accepted(self):
        self.payload["findings"][0]["decision"] = None
        self.assertNotIn("decision", validate_response(self.payload, self.evidence)[0])

    def test_decision_metadata_without_history_is_rejected(self):
        self.payload["findings"][0]["decision"] = {"decided": "x", "rejected": "unknown", "reason": "unknown", "status": "uncertain"}
        with self.assertRaisesRegex(ValueError, "cited historical"):
            validate_response(self.payload, self.evidence)

    def test_fabricated_source_and_quote_rejected(self):
        for ref in ({"id": "invented", "quote": "timeout"}, {"id": "code", "quote": "made up"}):
            self.payload["findings"][0]["evidence"][0] = ref
            with self.assertRaises(ValueError):
                validate_response(self.payload, self.evidence)

    def test_current_document_required(self):
        self.evidence[1]["type"] = "doc_base"
        with self.assertRaisesRegex(ValueError, "current documentation"):
            validate_response(self.payload, self.evidence)

    def test_repeated_document_quote_requires_unambiguous_context(self):
        self.evidence[1]['text'] = 'Example retries=3. Current default retries=3.'
        with self.assertRaisesRegex(ValueError, 'Ambiguous citation'):
            validate_response(self.payload, self.evidence)
        self.payload['findings'][0]['evidence'][1]['quote'] = 'Current default retries=3.'
        result = validate_response(self.payload, self.evidence)[0]
        self.assertEqual(result['evidence'][1]['column'], 20)

    def test_repeated_code_quote_requires_unambiguous_context(self):
        self.evidence[0]['text'] = 'old_timeout=30\nnew_timeout=30'
        with self.assertRaisesRegex(ValueError, 'Ambiguous citation'):
            validate_response(self.payload, self.evidence)

    def test_history_required_for_decision(self):
        self.payload["findings"][0]["kind"] = "decision_reminder"
        with self.assertRaisesRegex(ValueError, "discussion"):
            validate_response(self.payload, self.evidence)

    def test_sourced_decision_preserves_status_and_provenance(self):
        finding = self.payload["findings"][0]
        finding["kind"] = "decision_reminder"
        finding["evidence"] = [{"id": "code", "quote": "def fetch"},
                               {"id": "past", "quote": "We decided to keep fetch."}]
        finding["decision"] = {"decided": "Keep fetch", "rejected": "unknown", "reason": "unknown", "status": "recorded"}
        result = validate_response(self.payload, self.evidence)[0]
        self.assertEqual(result["decision"]["status"], "recorded")
        self.assertEqual(result["confidence"], "model_suggested")
        self.assertEqual(result["evidence"][1]["url"], "https://github.com/o/r/issues/1")

    def test_remote_endpoints_rejected(self):
        for url in ("https://example.org", "http://192.168.1.5:11434", "http://localhost.evil:11434", "http://user@localhost:11434"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Ollama(self.plan, url)

    def test_invalid_inventory_is_an_explicit_error(self):
        with patch.object(Ollama, 'request', return_value={'models': ['invalid']}):
            with self.assertRaisesRegex(ValueError, 'inventory'):
                Ollama(self.plan).ready()

    def test_invalid_evidence_id_is_an_explicit_error(self):
        self.payload['findings'][0]['evidence'][0]['id'] = []
        with self.assertRaisesRegex(ValueError, 'must be a string'):
            validate_response(self.payload, self.evidence)

    def test_transport_rejects_non_object_response(self):
        model = Ollama(self.plan)
        with patch.object(model.opener, 'open') as opened:
            opened.return_value.__enter__.return_value.read.return_value = b'[]'
            with self.assertRaisesRegex(ValueError, 'request failed'):
                model.request('/api/tags')

    def test_local_request_options_and_data_boundary(self):
        model = Ollama(self.plan)
        with patch.object(model, "request", return_value={"message": {"content": json.dumps(self.payload)}}) as request:
            result = model.review(self.evidence)
        body = request.call_args[0][1]
        self.assertEqual(body["options"]["num_ctx"], 8192)
        self.assertEqual(body["keep_alive"], 0)
        self.assertFalse(body["stream"])
        self.assertEqual(body["format"]["type"], "object")
        self.assertIn("UNTRUSTED DATA", body["messages"][0]["content"])
        self.assertEqual(len(result), 1)

    def test_output_truncation_is_error(self):
        with patch.object(Ollama, "request", return_value={"done_reason": "length"}):
            with self.assertRaisesRegex(ValueError, "output limit"):
                Ollama(self.plan).review(self.evidence)

    def test_malformed_response_is_error(self):
        with patch.object(Ollama, "request", return_value={"message": {"content": "not JSON"}}):
            with self.assertRaisesRegex(ValueError, "malformed JSON"):
                Ollama(self.plan).review(self.evidence)

    def test_prompt_overflow_rejected_before_request(self):
        with self.assertRaisesRegex(ValueError, "context budget"):
            Ollama(self.plan).review([{"text": "x" * 10000}])

    def test_cloud_alias_and_missing_model_rejected(self):
        model = Ollama(self.plan)
        with patch.object(model, "request", return_value={"models": []}):
            with self.assertRaisesRegex(ValueError, "not installed"):
                model.ready()
        with patch.object(model, "request", side_effect=[{"models": [{"name": self.plan["model"]}]}, {"remote_host": "cloud"}]):
            with self.assertRaisesRegex(ValueError, "Remote-backed"):
                model.ready()

    def test_one_validation_retry_then_success(self):
        bad = {"message": {"content": "{}"}}
        good = {"message": {"content": json.dumps(self.payload)}}
        model = Ollama(self.plan)
        with patch.object(model, "request", side_effect=[bad, good]) as request:
            self.assertEqual(len(model.review(self.evidence)), 1)
        self.assertEqual(request.call_count, 2)

    def test_retries_are_bounded(self):
        model = Ollama(self.plan)
        with patch.object(model, "request", return_value={"message": {"content": "{}"}}) as request:
            with self.assertRaises(ValueError):
                model.review(self.evidence)
        self.assertEqual(request.call_count, 2)

    def test_thinking_disabled_only_for_supported_models(self):
        model = Ollama(self.plan)
        with patch.object(model, "request", side_effect=[{"models": [{"name": self.plan["model"]}]}, {"capabilities": ["thinking"]}]):
            model.ready()
        with patch.object(model, "request", return_value={"message": {"content": "{\"findings\":[]}"}}) as request:
            model.review(self.evidence)
        self.assertFalse(request.call_args[0][1]["think"])


class ContradictionVerificationTests(unittest.TestCase):
    def test_consistent_docs_verdict_is_false(self):
        plan = select_model(Hardware('Darwin', 'arm64', 16, True))
        with patch.object(Ollama, 'request', return_value={'message': {'content': '{"contradiction":false}'}}):
            self.assertFalse(Ollama(plan).verify_contradiction({'summary': 'The documentation reflects the new signature.', 'evidence': []}))

    def test_contradiction_verdict_is_required(self):
        plan = select_model(Hardware('Darwin', 'arm64', 16, True))
        with patch.object(Ollama, 'request', return_value={'message': {'content': '{}'}}):
            with self.assertRaises(ValueError):
                Ollama(plan).verify_contradiction({'summary': 'unverified', 'evidence': []})
