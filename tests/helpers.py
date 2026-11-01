import subprocess
import tempfile
from pathlib import Path


class FixtureRepo:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test Developer")
        self.git("config", "user.email", "test@example.invalid")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.path)] + list(args), text=True, stderr=subprocess.DEVNULL).strip()

    def write(self, name, text):
        path = self.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def close(self):
        self.temp.cleanup()


OLD = "def fetch(url, retries=3):\n    return url\n"
NEW = "def fetch(url, timeout=30):\n    return url\n"
STALE_DOC = "# Fetch\n\n```python\nfrom client import fetch\nfetch('https://example.org', retries=3)\n```\n"
FRESH_DOC = STALE_DOC.replace("retries=3", "timeout=30")


def drift_repo():
    repo = FixtureRepo()
    repo.write("client.py", OLD)
    repo.write("README.md", STALE_DOC)
    base = repo.commit("Initial documented API")
    repo.write("client.py", NEW)
    head = repo.commit("Replace retries with timeout")
    return repo, base, head
