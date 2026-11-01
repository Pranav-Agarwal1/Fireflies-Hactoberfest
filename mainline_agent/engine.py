import difflib
import re
from datetime import datetime, timezone

from .prose import default_drift
from .checks import functions, imported_modules, modules_for, signature_findings
from .discovery import discover, is_code, relevant_sections, sections, text_windows


def clipped_source(identifier, kind, text, limit, **metadata):
    return dict(id=identifier, type=kind, text=text[:limit], truncated=len(text) > limit, **metadata)


def change_windows(old, new):
    before, after = old.splitlines(), new.splitlines()
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    for group in matcher.get_grouped_opcodes(3):
        a, b = group[0][1], group[-1][2]
        c, d = group[0][3], group[-1][4]
        yield a + 1, "\n".join(before[a:b]), c + 1, "\n".join(after[c:d])


def analyse(repo, base, plan, reviewer=None, history=None, max_files=30, max_docs=200, max_packets=40, include_gaps=False, initial=False, defer_model=False):
    if min(max_files, max_docs, max_packets) < 1:
        raise ValueError("Inspection limits must be positive.")
    changes = repo.changes(base)
    if initial:
        max_files = max(1, sum(is_code(path) for path in changes))
        max_docs = max(1, len(discover(repo, repo.head)['documents']))
    discovery = discover(repo, repo.head) if changes else {
        "documents": [], "configuration_files": [], "linked_candidates": [], "warnings": [],
        "scope": "No changes; document bodies were not inspected."}
    report = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "repository_path": str(repo.path), "base": base, "head": repo.head,
              "branch": "main", "model_plan": plan,
              "mode": "local_model_and_static" if reviewer else "static_only",
              "discovery": discovery, "findings": [], "errors": [],
              "warnings": list(discovery["warnings"]),
              "coverage": {"documents_read": 0, "code_files_read": 0, "model_packets": 0,
                           "skipped": [], "limits": {"files": max_files, "docs": max_docs, "packets": max_packets},
                           "scope": "Changed files, retrieved documentation, and supplied discussions only; not whole-repo proof."}}
    if initial:
        report["coverage"]["limits"]["packets"] = None
        report["coverage"]["scope"] = "First-run audit of all tracked supported source and documentation files; per-file limits still apply."
    if initial:
        from .discovery import ignored
        report["coverage"]["excluded_vendored_files"] = [p for p in changes if ignored(p)]
    report["changed_files"] = changes
    report["audit_type"] = "initial_repository_audit" if initial else "change_audit"
    if not changes:
        report["status"] = "completed"
        return report
    head_files, base_files = set(repo.files(repo.head)), set(repo.files(base))
    documents, candidates, prior_documents = [], [], {}
    doc_paths = sorted(discovery["documents"], key=lambda p: (p not in changes, p))
    report["coverage"]["skipped"].extend(doc_paths[max_docs:])
    for path in doc_paths[:max_docs]:
        try:
            text = repo.read(repo.head, path)
            documents.append({"path": path, "text": text})
            if path in changes:
                prior_documents[path] = repo.read(base, path) if path in base_files else ""
            candidates.extend(sections(text, path, repo.head))
            report["coverage"]["documents_read"] += 1
        except ValueError as exc:
            report["coverage"]["skipped"].append(path)
            report["warnings"].append(str(exc))
    if history:
        report["warnings"].extend(history.get("warnings", []))
    code_paths = [p for p in changes if is_code(p)]
    doc_only = [p for p in changes if p in doc_paths]
    imported = imported_modules([d for d in documents if d["path"] in doc_only])
    related = sorted(p for p in head_files if is_code(p) and p.endswith(".py") and modules_for(p) & imported)
    code_paths = list(dict.fromkeys(code_paths + related))
    report["coverage"]["skipped"].extend(code_paths[max_files:])
    if doc_only and not code_paths:
        report["warnings"].append("Changed documentation could not be mapped to code; not verified.")
        report["coverage"]["skipped"].extend(doc_only)
    for file_index, path in enumerate(code_paths[:max_files], 1):
        if initial and (file_index == 1 or file_index % 100 == 0 or file_index == len(code_paths[:max_files])):
            print("Initial audit %s/%s: %s" % (file_index, len(code_paths[:max_files]), path), flush=True)
        try:
            old = repo.read(base, path) if path in base_files else ""
            new = repo.read(repo.head, path) if path in head_files else ""
        except ValueError as exc:
            report["coverage"]["skipped"].append(path)
            report["warnings"].append(str(exc))
            continue
        report["coverage"]["code_files_read"] += 1
        findings, warnings = signature_findings(path, old, new, documents, base, repo.head, prior_documents)
        report["findings"].extend(findings)
        report["findings"].extend(default_drift(path, new if initial else old, new, documents, base, repo.head, initial=initial))
        if include_gaps and path.endswith(".py") and not warnings:
            before_names, after_names = functions(old), functions(new)
            for name in sorted(set(after_names) - set(before_names)):
                if not any(re.search(r"\b" + re.escape(name) + r"\b", d["text"]) for d in documents):
                    line = after_names[name][1]
                    report["findings"].append({"kind": "documentation_gap", "confidence": "needs_review",
                        "summary": "New public function %s has no exact name reference in %s inspected documentation files; review whether documentation is needed." % (name, len(documents)),
                        "evidence": [{"path": path, "sha": repo.head, "line": line, "quote": new.splitlines()[line - 1]}],
                        "limitation": "An exact-name search is a review hint, not proof of missing documentation."})
        report["warnings"].extend(warnings)
        if warnings:
            report["coverage"]["skipped"].append(path + ":static-analysis-unavailable")
        if not reviewer:
            if not path.endswith(".py") and not defer_model:
                report["coverage"]["skipped"].append(path + ":no-static-language-checker")
                report["warnings"].append("Static-only mode cannot verify " + path)
            continue
        windows = [(1, "", line, content) for line, content in text_windows(new, max(850, plan.get("context", 8192) - 8000))] if initial else list(change_windows(old, new))
        if not windows and path in related:
            windows = [(1, old, 1, new)]
        for index, (old_line, old_part, new_line, new_part) in enumerate(windows):
            if not initial and report["coverage"]["model_packets"] >= max_packets:
                report["coverage"]["skipped"].append(path + ":remaining-model-windows")
                break
            evidence = [clipped_source("code-base", "code_base", old_part or "[file or region absent]", 600,
                                       path=path, sha=base, line=old_line),
                        clipped_source("code-head", "code_head", new_part or "[file or region deleted]", max(900, plan.get("context", 8192) - 8000) if initial else 900,
                                       path=path, sha=repo.head, line=new_line)]
            relevant = relevant_sections(candidates, path, old_part, new_part, limit=1)
            for i, section in enumerate(relevant):
                evidence.append(clipped_source("doc-head-%s" % i, "doc_head", section["text"], 700,
                                               path=section["path"], sha=repo.head, line=section["line"]))
                if section["path"] in base_files:
                    try:
                        prior = sections(repo.read(base, section["path"]), section["path"], base)
                        matched = relevant_sections(prior, path, old_part, new_part, limit=1)
                        if matched:
                            evidence.append(clipped_source("doc-base-%s" % i, "doc_base", matched[0]["text"], 400,
                                                           path=section["path"], sha=base, line=matched[0]["line"]))
                    except ValueError as exc:
                        report["warnings"].append(str(exc))
            tokens = set(re.findall(r"[A-Za-z_][A-Za-z_0-9]{3,}", path + " " + old_part + " " + new_part))
            history_items = (history or {}).get("items", [])
            ranked = sorted(history_items, key=lambda item: -len(tokens & set(re.findall(r"[A-Za-z_][A-Za-z_0-9]{3,}", item["text"]))))
            for item in ranked[:1]:
                if tokens & set(re.findall(r"[A-Za-z_][A-Za-z_0-9]{3,}", item["text"])):
                    evidence.append(clipped_source(item["id"], "history", item["text"], 700, url=item["url"],
                                                   created_at=item.get("created_at"), updated_at=item.get("updated_at")))
            for item in evidence:
                if item["truncated"]:
                    report["warnings"].append("Evidence excerpt clipped: %s (%s, window %s)" % (path, item["id"], index))
            try:
                report["findings"].extend(reviewer.review(evidence))
            except ValueError as exc:
                report["errors"].append({"path": path, "window": index, "message": str(exc)})
            report["coverage"]["model_packets"] += 1
    # A single affected passage should produce one warning even when both checkers agree.
    unique = {}
    rank = {'verified_static': 0, 'possible_static': 1, 'model_suggested': 2, 'needs_review': 3}
    for finding in report['findings']:
        doc_refs = [e for e in finding['evidence'] if e.get('sha') == repo.head and e.get('path') in doc_paths]
        locations = tuple(sorted((e['path'], e.get('line'), e.get('quote', '').strip()) for e in doc_refs))
        code_refs = tuple(sorted({e['path'] for e in finding['evidence'] if e.get('path') in changes and is_code(e['path'])}))
        key = (finding['kind'], locations, code_refs) if locations else ('unlocated', len(unique))
        previous = unique.get(key)
        if previous is None or rank.get(finding['confidence'], 9) < rank.get(previous['confidence'], 9):
            unique[key] = finding
    report['findings'] = list(unique.values())
    for finding in report['findings']:
        code_paths = {e.get('path') for e in finding['evidence'] if e.get('sha') == base}
        responsible = [p for p in changes if is_code(p) and (p in code_paths or any(e.get('path') == p for e in finding['evidence']))]
        finding['code_changes'] = [] if initial else [{'path': p, 'base': base, 'head': repo.head, 'patch': repo.patch(base, p)} for p in responsible]
    report["warnings"] = list(dict.fromkeys(report["warnings"]))
    report["status"] = "incomplete" if report["errors"] else "completed_with_limits" if report["warnings"] or report["coverage"]["skipped"] else "completed"
    return report
