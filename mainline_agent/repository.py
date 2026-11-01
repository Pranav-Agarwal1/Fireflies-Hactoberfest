"""Git snapshots pinned to the first-parent history of main; no checkout."""
import re
import os
import subprocess
from pathlib import Path
from .discovery import decode_source


def git(path, *args, binary=False):
    allowed = {"rev-parse", "rev-list", "ls-tree", "cat-file", "diff", "config"}
    if not args or args[0] not in allowed:
        raise ValueError("Offline Git operation is not allowed.")
    environment = dict(os.environ, GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0",
                       GIT_NO_REPLACE_OBJECTS="1", GIT_ALLOW_PROTOCOL="")
    command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
               "-c", "diff.external=", "-C", str(path)] + list(args)
    try:
        result = subprocess.run(command, capture_output=True, timeout=120, env=environment)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Git could not run or timed out (%s)." % type(exc).__name__) from exc
    if result.returncode:
        error = result.stderr.decode("utf-8", "replace").strip()
        raise ValueError("Git operation failed: " + error)
    return result.stdout if binary else result.stdout.decode("utf-8", "replace").strip()



class Repository:
    def __init__(self, path, at=None):
        self.path = Path(path).resolve()
        self._entries = {}
        self._blobs = {}
        partial = git(self.path, "config", "--local", "--list")
        if any(".promisor=" in line.lower() or line.lower().startswith("extensions.partialclone=") for line in partial.splitlines()):
            raise ValueError("Partial clones are not supported offline; provide a complete local Git object database.")
        try:
            self.head = git(self.path, "rev-parse", "--verify", "refs/heads/main^{commit}")
        except ValueError as exc:
            raise ValueError("No local main branch; provide a complete local copy of main first. " + str(exc)) from exc
        self.history = git(self.path, "rev-list", "--first-parent", self.head).splitlines()
        if at is not None:
            self.head = self.baseline(at)
            self.history = self.history[self.history.index(self.head):]

    def baseline(self, requested=None):
        if requested is not None:
            if not isinstance(requested, str) or not re.fullmatch(r"[0-9a-fA-F]{7,64}", requested):
                raise ValueError("Baseline must be a commit SHA on main's first-parent history.")
            base = git(self.path, "rev-parse", "--verify", requested + "^{commit}")
        elif len(self.history) > 1:
            base = self.history[1]
        else:
            raise ValueError("main has no previous commit. Supply a repository with two main snapshots.")
        if base not in self.history:
            raise ValueError("Baseline is not on current main's first-parent history (rewrite or unmerged branch).")
        return base

    def entries(self, sha):
        if sha not in self._entries:
            raw = git(self.path, 'ls-tree', '-r', '-l', '-z', sha, binary=True)
            entries = {}
            for record in raw.split(b'\0'):
                if not record:
                    continue
                meta, name = record.split(b'\t', 1)
                mode, kind, oid, size = meta.split()
                if mode in (b'100644', b'100755'):
                    entries[name.decode('utf-8', 'replace')] = (oid.decode(), int(size))
            self._entries[sha] = entries
        return self._entries[sha]

    def files(self, sha):
        return list(self.entries(sha))

    def prefetch(self, sha, limit=100000, paths=None):
        from .discovery import is_code, is_doc
        entries = self.entries(sha)
        groups, current, size = [], [], 0
        selected = set(paths) if paths is not None else None
        for path, (oid, length) in entries.items():
            if selected is not None and path not in selected:
                continue
            if length > limit or oid in self._blobs or not (is_code(path) or is_doc(path)):
                continue
            if current and size + length > 16 * 1024**2:
                groups.append(current)
                current, size = [], 0
            current.append((oid, length))
            size += length
        if current:
            groups.append(current)
        for group in groups:
            # communicate drains stdout while sending stdin, avoiding pipe deadlock.
            command = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                       '-C', str(self.path), 'cat-file', '--batch']
            environment = dict(os.environ, GIT_NO_LAZY_FETCH='1', GIT_ALLOW_PROTOCOL='', GIT_NO_REPLACE_OBJECTS='1')
            try:
                result = subprocess.run(command, input=''.join(oid + '\n' for oid, _ in group).encode(),
                                        capture_output=True, timeout=120, env=environment)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ValueError('Local Git batch read failed.') from exc
            if result.returncode:
                raise ValueError('Local Git batch read failed.')
            cursor = 0
            for oid, length in group:
                end = result.stdout.find(b'\n', cursor)
                expected = ('%s blob %s' % (oid, length)).encode()
                if end < 0 or result.stdout[cursor:end] != expected:
                    raise ValueError('Unexpected Git batch response.')
                cursor = end + 1
                data = result.stdout[cursor:cursor + length]
                if len(data) != length or result.stdout[cursor + length:cursor + length + 1] != b'\n':
                    raise ValueError('Truncated Git batch response.')
                self._blobs[oid] = data
                cursor += length + 1

    def read(self, sha, path, limit=100000):
        oid, size = self.entries(sha)[path]
        if size > limit:
            raise ValueError('File exceeds %s-byte inspection limit: %s' % (limit, path))
        data = self._blobs.get(oid)
        if data is None:
            data = git(self.path, 'cat-file', 'blob', oid, binary=True)
            self._blobs[oid] = data
        return decode_source(data, path)

    def changes(self, base):
        raw = git(self.path, "diff", "--name-only", "--no-renames", "-z", base, self.head, "--", binary=True)
        return [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]

    def patch(self, base, path):
        return git(self.path, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--unified=3",
                   base, self.head, "--", path)
