"""Create a small Git repository with intentional main-to-main documentation drift."""
import argparse
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", help="A new or empty directory")
    args = parser.parse_args()
    root = Path(args.directory).resolve()
    if root.exists() and any(root.iterdir()):
        parser.error("Choose a new or empty directory; existing files will not be changed.")
    root.mkdir(parents=True, exist_ok=True)
    def git(*command):
        return subprocess.check_output(["git", "-C", str(root)] + list(command), text=True, stderr=subprocess.DEVNULL).strip()
    git("init", "-b", "main")
    git("config", "user.name", "Mainline Demo")
    git("config", "user.email", "demo@example.invalid")
    (root / "client.py").write_text("def fetch(url, retries=3):\n    return url\n", encoding="utf-8")
    (root / "README.md").write_text("# Fetch API\n\n```python\nfrom client import fetch\nfetch('https://example.org', retries=3)\n```\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "Document retries argument")
    base = git("rev-parse", "HEAD")
    git("checkout", "-b", "update-client")
    (root / "client.py").write_text("def fetch(url, timeout=30):\n    return url\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "Replace retries with timeout without updating docs")
    git("checkout", "main")
    git("merge", "--no-ff", "update-client", "-m", "Merge client update")
    print("Created demo: " + str(root))
    print("Base: " + base)
    print("Expected: one verified_static documentation_drift finding; scan exit code 1.")


if __name__ == "__main__":
    main()
