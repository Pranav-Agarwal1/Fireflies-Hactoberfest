import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from mainline_agent.cli import main
from mainline_agent.discovery import discover
from mainline_agent.engine import analyse
from mainline_agent.hardware import Hardware, select_model
from mainline_agent.repository import Repository
from tests.helpers import FRESH_DOC, drift_repo


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.fixture, self.base, self.head = drift_repo()
        self.addCleanup(self.fixture.close)
        self.plan = select_model(Hardware("Darwin", "arm64", 16, True))

    def scan(self, *extra):
        args = ["scan", "--path", str(self.fixture.path), "--no-llm", "--ram-gb", "16", "--unified",
                "--out", str(self.fixture.path / "reports")]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(args + list(extra))

    def test_end_to_end_offline_static_scan_and_report(self):
        self.assertEqual(self.scan(), 1)
        report = json.loads((self.fixture.path / "reports/analysis.log").read_text())
        self.assertEqual(report["base"], self.base)
        self.assertEqual(report["head"], self.head)
        self.assertEqual(report["findings"][0]["kind"], "documentation_drift")
        self.assertTrue((self.fixture.path / "reports/maintainer.log").exists())

    def test_docs_updated_with_code_pass(self):
        self.fixture.write("README.md", FRESH_DOC)
        self.fixture.commit("Fix docs")
        self.assertEqual(self.scan("--base", self.base), 0)

    def test_state_prevents_repeating_successfully_analysed_commits(self):
        state = self.fixture.path / "state.json"
        self.assertEqual(self.scan("--state", str(state)), 1)
        self.assertEqual(json.loads(state.read_text())["head"], self.head)
        self.assertEqual(self.scan("--state", str(state)), 0)

    def test_repository_identity_mismatch_rejected(self):
        state = self.fixture.path / "state.json"
        state.write_text(json.dumps({"repository_path": "/unrelated", "head": self.base}))
        self.assertEqual(self.scan("--state", str(state)), 2)

    def test_model_failure_is_reported_not_clean(self):
        class Broken:
            def review(self, evidence):
                raise ValueError("fabricated citation")
        report = analyse(Repository(self.fixture.path), self.base, self.plan, Broken())
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("fabricated", report["errors"][0]["message"])

    def test_model_sees_both_main_snapshots_and_current_docs(self):
        packets = []
        class Reviewer:
            def review(self, evidence):
                packets.append(evidence)
                return []
        report = analyse(Repository(self.fixture.path), self.base, self.plan, Reviewer())
        types = {e["type"] for e in packets[0]}
        self.assertTrue({"code_base", "code_head", "doc_head", "doc_base"}.issubset(types))
        self.assertEqual(report["coverage"]["model_packets"], 1)

    def test_matching_history_is_included_in_evidence_packet(self):
        packets = []
        class Reviewer:
            def review(self, evidence):
                packets.append(evidence)
                return []
        history = {"items": [{"id": "decision-1", "text": "We decided to keep fetch retries for network failures.",
                               "url": "https://github.com/o/r/issues/1"}]}
        analyse(Repository(self.fixture.path), self.base, self.plan, Reviewer(), history)
        self.assertEqual([e["id"] for e in packets[0] if e["type"] == "history"], ["decision-1"])

    def test_limits_are_visible_and_prevent_baseline_advance(self):
        self.fixture.write("docs/more.md", "extra docs")
        self.fixture.commit("More docs")
        state = self.fixture.path / "state.json"
        self.assertEqual(self.scan("--base", self.base, "--max-docs", "1", "--state", str(state)), 2)
        self.assertFalse(state.exists())

    def test_discovery_tracks_config_links_and_skips_symlinks(self):
        self.fixture.write("mkdocs.yml", "site_url: https://docs.example.org\n")
        self.fixture.write("docs/guide.rst", "Guide")
        (self.fixture.path / "docs/outside.md").symlink_to("/etc/passwd")
        self.fixture.commit("Docs configuration")
        repo = Repository(self.fixture.path)
        info = discover(repo, repo.head)
        self.assertIn("docs/guide.rst", info["documents"])
        self.assertNotIn("docs/outside.md", info["documents"])
        self.assertEqual(info["linked_candidates"][0]["status"], "candidate_only_not_fetched")

    def test_document_only_fixed_example_passes(self):
        self.fixture.write("README.md", FRESH_DOC)
        self.fixture.commit("Docs only")
        self.assertEqual(self.scan(), 0)

    def test_document_only_new_invalid_example_is_caught(self):
        self.fixture.write("README.md", FRESH_DOC.replace("timeout=30", "bogus=30"))
        self.fixture.commit("New invalid example")
        self.assertEqual(self.scan(), 1)

    def test_unmapped_docs_are_not_claimed_verified(self):
        self.fixture.write("README.md", "# Architecture\nNew architecture claim with no code link.")
        self.fixture.commit("Unmapped prose")
        self.assertEqual(self.scan(), 2)

    def test_doctor_simulation_needs_no_model_or_network(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["doctor", "--ram-gb", "16", "--unified"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["model"], "qwen2.5-coder:7b")

    def test_model_failure_does_not_advance_baseline(self):
        state = self.fixture.path / "state.json"
        with patch("mainline_agent.cli.Ollama.ready"), patch("mainline_agent.cli.Ollama.review", side_effect=ValueError("bad evidence")):
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["scan", "--path", str(self.fixture.path), "--ram-gb", "16", "--unified",
                             "--state", str(state), "--out", str(self.fixture.path / "reports")])
        self.assertEqual(code, 2)
        self.assertFalse(state.exists())

    def test_non_python_static_scan_reports_uncovered_changes(self):
        self.fixture.write("parser.c", "int parse(void) { return 0; }\n")
        self.fixture.commit("C implementation")
        self.assertEqual(self.scan(), 2)
