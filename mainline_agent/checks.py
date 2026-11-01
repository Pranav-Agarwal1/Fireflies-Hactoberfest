"""Python signature checks use ASTs and never import repository modules."""
import ast
import inspect
import re
from pathlib import PurePosixPath


def functions(text):
    tree = ast.parse(text)
    result = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_") or node.decorator_list:
            continue
        args = node.args
        parameters = []
        positional = list(args.posonlyargs) + list(args.args)
        required = len(positional) - len(args.defaults)
        for i, arg in enumerate(positional):
            kind = inspect.Parameter.POSITIONAL_ONLY if i < len(args.posonlyargs) else inspect.Parameter.POSITIONAL_OR_KEYWORD
            parameters.append(inspect.Parameter(arg.arg, kind, default=inspect.Parameter.empty if i < required else None))
        if args.vararg:
            parameters.append(inspect.Parameter(args.vararg.arg, inspect.Parameter.VAR_POSITIONAL))
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            parameters.append(inspect.Parameter(arg.arg, inspect.Parameter.KEYWORD_ONLY,
                                               default=inspect.Parameter.empty if default is None else None))
        if args.kwarg:
            parameters.append(inspect.Parameter(args.kwarg.arg, inspect.Parameter.VAR_KEYWORD))
        result[node.name] = (inspect.Signature(parameters), node.lineno)
    return result


def modules_for(path):
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    # Support conventional src/ layouts; do not match arbitrary filename suffixes.
    if parts and parts[0] == "src":
        parts.pop(0)
    return {".".join(parts)}


def imported_modules(documents):
    modules = set()
    for doc in documents:
        for block in re.finditer(r"^```(?:python|py)\s*\n(.*?)^```", doc["text"], re.M | re.S):
            try:
                tree = ast.parse(block.group(1))
            except SyntaxError:
                continue
            for node in tree.body:
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    modules.add(node.module)
                elif isinstance(node, ast.Import):
                    modules.update(a.name for a in node.names)
    return modules


def _calls(text):
    calls = set()
    for block in re.finditer(r"^```(?:python|py)\s*\n(.*?)^```", text, re.M | re.S):
        try:
            calls.update(ast.dump(n) for n in ast.walk(ast.parse(block.group(1))) if isinstance(n, ast.Call))
        except SyntaxError:
            pass
    return calls


def signature_findings(path, old, new, documents, base, head, prior_documents=None):
    if not path.endswith(".py"):
        return [], []
    try:
        before, after = functions(old), functions(new)
    except (SyntaxError, ValueError) as exc:
        return [], ["Python signature analysis unavailable for %s: %s" % (path, exc)]
    findings = []
    current_tree = ast.parse(new)
    bound = {n.name for n in ast.walk(current_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    bound.update(n.id for n in ast.walk(current_tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
    for node in ast.walk(current_tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update(a.asname or a.name.split('.')[0] for a in node.names)
    modules = modules_for(path)
    for doc in documents:
        prior_calls = _calls((prior_documents or {}).get(doc["path"], ""))
        # Fenced Python examples only. A failed parse is explicitly uncovered.
        for block in re.finditer(r"^```(?:python|py)\s*\n(.*?)^```", doc["text"], re.M | re.S):
            try:
                tree = ast.parse(block.group(1))
            except SyntaxError:
                continue
            aliases, module_aliases = {}, set()
            for node in tree.body:
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in modules:
                    aliases.update({a.asname or a.name: a.name for a in node.names})
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name in modules:
                            module_aliases.add(a.asname or a.name)
            for call in ast.walk(tree):
                if not isinstance(call, ast.Call) or any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
                    continue
                target = None
                if isinstance(call.func, ast.Name):
                    target = aliases.get(call.func.id)
                elif isinstance(call.func, ast.Attribute):
                    try:
                        prefix = ast.unparse(call.func.value)
                    except AttributeError:
                        prefix = ""
                    if prefix in module_aliases:
                        target = call.func.attr
                if target not in after:
                    if target in before and target not in bound and "*" not in bound:
                        replacements = sorted(set(after) - set(before))
                        summary = "Documentation calls %s, whose declaration was removed from %s." % (target, path)
                        if replacements:
                            summary += " New declarations to review as possible replacements: " + ", ".join(replacements)
                        findings.append({"kind": "documentation_drift", "confidence": "possible_static",
                                         "summary": summary,
                                         "evidence": [{"path": doc["path"], "sha": head,
                                                       "line": doc["text"].count("\n", 0, block.start(1)) + call.lineno,
                                                       "quote": ast.get_source_segment(block.group(1), call) or target},
                                                      {"path": path, "sha": base, "line": before[target][1],
                                                       "quote": old.splitlines()[before[target][1] - 1]}],
                                         "replacement_candidates": replacements,
                                         "limitation": "Removed declaration verified; runtime re-exports and rename identity require review."})
                    continue
                old_sig = before[target][0] if target in before else None
                new_sig, code_line = after[target]
                new_example = prior_documents is not None and doc["path"] in prior_documents and ast.dump(call) not in prior_calls
                if old_sig == new_sig and not new_example:
                    continue
                args = [None] * len(call.args)
                kwargs = {k.arg: None for k in call.keywords}
                try:
                    if old_sig is not None:
                        old_sig.bind(*args, **kwargs)
                    elif not new_example:
                        continue
                except TypeError:
                    if not new_example:
                        continue  # Was already invalid before this main-to-main change.
                try:
                    new_sig.bind(*args, **kwargs)
                except TypeError as exc:
                    doc_line = doc["text"].count("\n", 0, block.start(1)) + call.lineno
                    evidence = [
                        {"path": doc["path"], "sha": head, "line": doc_line,
                         "quote": ast.get_source_segment(block.group(1), call) or target},
                        {"path": path, "sha": head, "line": code_line,
                         "quote": new.splitlines()[code_line - 1]}]
                    if target in before:
                        evidence.append({"path": path, "sha": base, "line": before[target][1],
                                         "quote": old.splitlines()[before[target][1] - 1]})
                    findings.append({"kind": "documentation_drift", "confidence": "verified_static",
                                     "summary": "%s example no longer binds to its declared signature: %s" % (target, exc),
                                     "evidence": evidence,
                                     "limitation": "Static declared-signature check; dynamic rebinding is not evaluated."})
    return findings, []
