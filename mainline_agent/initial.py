"""Current-state snapshot adapter for the first repository-wide audit."""
from .github import GitHubSnapshot


EMPTY = '0' * 40


class InitialSnapshot:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.head, self.path = snapshot.head, snapshot.path

    def files(self, sha):
        return [] if sha == EMPTY else self.snapshot.files(sha)

    def read(self, sha, path, limit=None):
        return self.snapshot.read(sha, path) if limit is None else self.snapshot.read(sha, path, limit)

    def changes(self, base):
        return self.files(self.head)

    def patch(self, base, path):
        # Current-state inconsistencies have no known historical introducing patch.
        return ''


def current_snapshot(api, head, cache):
    return InitialSnapshot(GitHubSnapshot(api, head, head, cache))


def audit_repository(snapshot, base, plan, reviewer, max_sections=None, progress=None):
    """Index all supported files, then audit each documentation window against code."""
    import re
    import time
    from collections import Counter, defaultdict
    from heapq import nsmallest
    from pathlib import PurePosixPath
    from .engine import analyse
    from .discovery import is_code, sections, text_windows
    if max_sections is not None and max_sections < 1:
        raise ValueError('Section limit must be positive.')
    started = time.monotonic()
    if hasattr(snapshot, 'snapshot') and hasattr(snapshot.snapshot, 'prefetch'):
        snapshot.snapshot.prefetch(snapshot.head, paths=snapshot.files(snapshot.head))
    report = analyse(snapshot, base, plan, initial=True, defer_model=True)
    report['timings'] = {'static_index_seconds': round(time.monotonic() - started, 3)}
    indexed = time.monotonic()
    if reviewer is None:
        if any(not path.endswith('.py') for path in snapshot.files(snapshot.head) if is_code(path)):
            report['coverage']['skipped'].append('Non-Python semantic analysis requires the local model.')
            report['status'] = 'completed_with_limits'
        return report
    code = []
    stop = {'public', 'private', 'return', 'void', 'true', 'false', 'null', 'using', 'name', 'value', 'class', 'GameJam', 'Assets', 'Scripts'}
    def words(text):
        return set(re.findall(r'[A-Za-z_][A-Za-z_0-9]{2,}', text)) - stop
    window_size = 650 if plan['context'] < 16000 else 1000
    for path in snapshot.files(snapshot.head):
        if not is_code(path):
            continue
        try:
            text = snapshot.read(snapshot.head, path)
        except ValueError:
            continue  # Already recorded by the indexing/static phase.
        for line, content in text_windows(text, window_size):
            code.append((words(path + ' ' + content), {'path': path, 'sha': snapshot.head, 'line': line, 'text': content}))
    documents = []
    for path in report['discovery']['documents']:
        try:
            documents.extend(sections(snapshot.read(snapshot.head, path), path, snapshot.head))
        except ValueError:
            continue
    postings = defaultdict(list)
    for position, (tokens, _) in enumerate(code):
        for token in tokens:
            postings[token].append(position)
    report['timings']['retrieval_index_seconds'] = round(time.monotonic() - indexed, 3)
    report['timings']['retrieval_seconds'] = 0.0
    report['timings']['local_model_seconds'] = 0.0
    report['coverage']['indexed_code_windows'] = len(code)
    report['coverage']['unmapped_documentation'] = []
    report['coverage']['documentation_sections_total'] = len(documents)
    report['coverage']['documentation_sections_reviewed'] = 0
    if progress:
        progress(report)
    for index, document in enumerate(documents, 1):
        if max_sections is not None and index > max_sections:
            report['coverage']['skipped'].append('%s documentation sections not reviewed by the requested limit.' % (len(documents) - max_sections))
            report['coverage']['partial'] = True
            break
        retrieval_started = time.monotonic()
        query = words(document['text'] + ' ' + document['path'])
        scores = Counter()
        for token in query:
            scores.update(postings.get(token, ()))
        positions = nsmallest(3 if plan['context'] < 16000 else 4, scores,
                              key=lambda position: (-scores[position], position))
        matches = [code[position][1] for position in positions]
        report['timings']['retrieval_seconds'] += time.monotonic() - retrieval_started
        if not matches:
            report['coverage']['unmapped_documentation'].append({'path': document['path'], 'line': document['line']})
            report['coverage']['documentation_sections_reviewed'] = index
            continue
        print('Initial documentation review %s/%s: %s:%s' % (index, len(documents), document['path'], document['line']), flush=True)
        evidence = [dict(document, id='doc-head', type='doc_head')]
        for n, item in enumerate(matches):
            evidence.append(dict(item, id='code-head' if n == 0 else 'code-head-%s' % n, type='code_head'))
        model_started = time.monotonic()
        try:
            findings = reviewer.review(evidence)
            for finding in findings:
                finding['code_changes'] = []
                report['findings'].append(finding)
        except ValueError as exc:
            report['errors'].append({'path': document['path'], 'line': document['line'], 'message': str(exc)})
        report['coverage']['model_packets'] += 1
        report['coverage']['documentation_sections_reviewed'] = index
        report['timings']['local_model_seconds'] += time.monotonic() - model_started
        if progress and (index == 1 or index % 5 == 0):
            progress(report)
    report['mode'] = 'local_model_and_static'
    report['status'] = 'incomplete' if report['errors'] else 'completed_with_limits' if report['warnings'] or report['coverage']['skipped'] else 'completed'
    return report


class ScopedLocalSnapshot(InitialSnapshot):
    def __init__(self, snapshot, includes):
        super().__init__(snapshot)
        self.includes = includes

    def files(self, sha):
        if sha == EMPTY:
            return []
        paths = self.snapshot.files(sha)
        if not self.includes:
            return paths
        return [path for path in paths if any(path == prefix or path.startswith(prefix.rstrip('/') + '/')
                                              for prefix in self.includes)]
