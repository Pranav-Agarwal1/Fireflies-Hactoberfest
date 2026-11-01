import unittest

from mainline_agent.repository import Repository, git
from tests.helpers import FixtureRepo, drift_repo


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture, self.base, self.head = drift_repo()
        self.addCleanup(self.fixture.close)

    def test_freezes_head_and_reads_main_while_on_feature(self):
        self.fixture.git("checkout", "-b", "feature")
        self.fixture.write("client.py", "NOT MAIN")
        self.fixture.commit("Feature only")
        repo = Repository(self.fixture.path)
        self.assertEqual(repo.head, self.head)
        self.assertNotIn("NOT MAIN", repo.read(repo.head, "client.py"))
        self.assertEqual(repo.baseline(), self.base)

    def test_rejects_unmerged_baseline(self):
        self.fixture.git("checkout", "-b", "feature")
        self.fixture.write("feature.py", "x = 1")
        other = self.fixture.commit("Unmerged")
        with self.assertRaisesRegex(ValueError, "first-parent"):
            Repository(self.fixture.path).baseline(other)

    def test_rejects_merged_side_parent_as_main_baseline(self):
        self.fixture.git("checkout", "-b", "feature")
        self.fixture.write("feature.py", "x = 1")
        side = self.fixture.commit("Feature")
        self.fixture.git("checkout", "main")
        self.fixture.git("merge", "--no-ff", "feature", "-m", "Merge feature")
        repo = Repository(self.fixture.path)
        self.assertEqual(repo.baseline(), self.head)
        with self.assertRaisesRegex(ValueError, "first-parent"):
            repo.baseline(side)

    def test_changed_paths_and_binary_limits(self):
        repo = Repository(self.fixture.path)
        self.assertEqual(repo.changes(self.base), ["client.py"])
        with self.assertRaisesRegex(ValueError, "limit"):
            repo.read(self.head, "client.py", limit=2)

    def test_missing_main_is_error(self):
        self.fixture.git("branch", "-m", "develop")
        with self.assertRaisesRegex(ValueError, "No local main"):
            Repository(self.fixture.path)

    def test_network_git_operations_are_disabled(self):
        for op in ("fetch", "clone", "pull", "push", "ls-remote"):
            with self.subTest(op=op), self.assertRaisesRegex(ValueError, "Offline"):
                git(self.fixture.path, op, "https://example.invalid/repo.git")

    def test_partial_clone_is_rejected_before_object_access(self):
        self.fixture.git("config", "remote.origin.promisor", "true")
        with self.assertRaisesRegex(ValueError, "Partial clones"):
            Repository(self.fixture.path)

    def test_batch_prefetch_preserves_content_and_avoids_repeat_reads(self):
        from unittest.mock import patch
        repo = Repository(self.fixture.path)
        repo.prefetch(repo.head)
        expected = repo.read(repo.head, 'client.py')
        with patch('mainline_agent.repository.git', side_effect=AssertionError('Unexpected repeat Git process')):
            self.assertEqual(repo.read(repo.head, 'client.py'), expected)
            self.assertIn('client.py', repo.files(repo.head))

    def test_batch_prefetch_respects_selected_paths(self):
        repo = Repository(self.fixture.path)
        repo.prefetch(repo.head, paths=['client.py'])
        doc_oid = repo.entries(repo.head)['README.md'][0]
        self.assertNotIn(doc_oid, repo._blobs)
