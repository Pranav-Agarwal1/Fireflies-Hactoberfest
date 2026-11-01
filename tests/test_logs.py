import copy
import json
import tempfile
import unittest
from pathlib import Path
from mainline_agent.report import write_report
from mainline_agent.engine import analyse
from mainline_agent.hardware import Hardware, select_model
from mainline_agent.repository import Repository
from tests.helpers import drift_repo


class MaintainerLogTests(unittest.TestCase):
    def setUp(self):
        self.fixture, self.base, self.head = drift_repo()
        self.addCleanup(self.fixture.close)
        self.report = analyse(Repository(self.fixture.path), self.base, select_model(Hardware('Darwin','arm64',16,True)))
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.out = Path(self.storage.name)

    def test_only_log_files_are_generated(self):
        write_report(self.out, self.report)
        self.assertEqual({p.name for p in self.out.iterdir()}, {'maintainer.log','analysis.log'})

    def test_human_log_includes_quotation_code_and_revision(self):
        write_report(self.out,self.report)
        text = (self.out/'maintainer.log').read_text()
        for expected in ('README.md','client.py','retries',self.head,self.base,'Coverage'):
            self.assertIn(expected,text)

    def test_structured_log_preserves_full_report(self):
        write_report(self.out,self.report)
        self.assertEqual(json.loads((self.out/'analysis.log').read_text()),self.report)

    def test_resolved_scan_appends_history_and_replaces_latest_snapshot(self):
        write_report(self.out,self.report)
        original = (self.out/'maintainer.log').read_text()
        clean=copy.deepcopy(self.report);clean['findings']=[]
        write_report(self.out,clean)
        self.assertTrue((self.out/'maintainer.log').read_text().startswith(original))
        self.assertIn('No discrepancies established',(self.out/'maintainer.log').read_text())
        self.assertEqual(json.loads((self.out/'analysis.log').read_text())['findings'],[])

    def test_incomplete_results_keep_errors_visible(self):
        self.report['errors']=[{'message':'Local model unavailable'}]
        write_report(self.out,self.report)
        self.assertIn('Local model unavailable',(self.out/'maintainer.log').read_text())

    def test_repository_and_event_are_visible(self):
        self.report.update(repository='owner/repository',event='merged_pr',pull_request=42)
        write_report(self.out,self.report)
        text=(self.out/'maintainer.log').read_text()
        for expected in ('owner/repository','merged_pr','Pull request: #42'):
            self.assertIn(expected,text)
