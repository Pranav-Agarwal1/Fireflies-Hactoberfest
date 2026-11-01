"""Discover tracked documentation and official-link candidates without crawling."""
import re
from pathlib import PurePosixPath

DOC_EXTENSIONS = {".md", ".mdx", ".rst", ".adoc"}
CONFIGS = {"mkdocs.yml", "mkdocs.yaml", "docusaurus.config.js", "docusaurus.config.ts",
           "conf.py", "_config.yml", "book.toml", "antora.yml", ".readthedocs.yaml"}
IGNORED = {"node_modules", "vendor", "dist", "build", ".venv", "venv", "TextMesh Pro"}
BINARY_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.bmp', '.mp3', '.mp4', '.wav',
                     '.ogg', '.ttf', '.otf', '.woff', '.woff2', '.pdf', '.zip', '.gz', '.bz2', '.xz',
                     '.7z', '.tar', '.exe', '.dll', '.so', '.dylib', '.pyc', '.class', '.jar', '.mo',
                     '.db', '.sqlite', '.sqlite3', '.blend', '.fbx', '.psd'}


def ignored(path):
    return bool(set(PurePosixPath(path).parts) & IGNORED)


def is_doc(path):
    p = PurePosixPath(path)
    return not ignored(path) and (p.suffix.lower() in DOC_EXTENSIONS or p.name.lower().startswith("readme")
                                 or p.suffix == ".txt" and any(x in p.parts for x in ("doc", "docs", "documentation")))


def is_code(path):
    return not ignored(path) and not is_doc(path) and PurePosixPath(path).suffix.lower() not in BINARY_EXTENSIONS


def discover(repo, sha):
    files = repo.files(sha)
    docs = sorted(p for p in files if is_doc(p))
    configs = sorted(p for p in files if not ignored(p) and (PurePosixPath(p).name in CONFIGS
                     or p.startswith(".github/workflows/")))
    candidates, warnings = [], []
    for path in sorted(set(configs + [p for p in docs if PurePosixPath(p).name.lower().startswith("readme")])):
        try:
            text = repo.read(sha, path)
        except ValueError as exc:
            warnings.append(str(exc))
            continue
        for match in re.finditer(r"https?://[^\s<>\)\]\"']+", text):
            url = match.group().rstrip(".,;")
            if re.search(r"doc|readthedocs|github\.com", url, re.I):
                candidates.append({"url": url, "source_path": path,
                                   "line": text.count("\n", 0, match.start()) + 1,
                                   "status": "candidate_only_not_fetched"})
    return {"documents": docs, "configuration_files": configs, "linked_candidates": candidates,
            "warnings": warnings,
            "scope": "Tracked documentation on main only. External links require explicit future configuration."}


def text_windows(text, limit=650):
    """Bounded windows with original line positions and no discarded characters."""
    line = 1
    start = 1
    pending = ''
    for part in text.splitlines(keepends=True):
        if pending and len(pending) + len(part) > limit:
            yield start, pending
            pending = ''
        while len(part) > limit:
            yield line, part[:limit]
            part = part[limit:]
        if not pending:
            start = line
        pending += part
        line += part.count('\n')
    if pending:
        yield start, pending


def sections(text, path, sha):
    return [{'path': path, 'sha': sha, 'line': line, 'text': content}
            for line, content in text_windows(text) if content.strip()]


def relevant_sections(candidates, path, old, new, limit=4):
    # No embedding service needed. Exact identifiers/path terms first; model handles semantics.
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", path + " " + old + " " + new))
    tokens -= {"return", "self", "None", "True", "False", "import", "from", "class", "def", "const"}
    scored = []
    for item in candidates:
        words = set(re.findall(r"[A-Za-z_][A-Za-z_0-9]{2,}", item["text"] + " " + item["path"]))
        score = len(tokens & words)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda x: (-x[0], x[1]["path"], x[1]["line"]))
    return [item for _, item in scored[:limit]]


def decode_source(data, path):
    if b'\0' in data or any(byte < 9 or 13 < byte < 32 for byte in data[:8192]):
        raise ValueError('Binary/control data cannot be analysed as source: ' + path)
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('Unsupported text encoding; supply UTF-8 source: ' + path) from exc
