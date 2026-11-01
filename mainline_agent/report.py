import json
import os
import tempfile
from pathlib import Path


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = stream.name
        try:
            json.dump(value, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
        except Exception:
            os.unlink(temporary)
            raise
    os.replace(temporary, path)


def safe(value):
    return str(value).replace("\n", " ").replace("\r", " ").replace("<", "&lt;").replace(">", "&gt;")


def markdown(report):
    lines = ["# Mainline Agent report", "", "Status: **%s**" % report["status"], "",
             "Branch: `%s` · Base: `%s` · Head: `%s`" % (safe(report.get("branch", "main")), report["base"], report["head"]),
             "", "Mode: `%s` · Model: `%s`" % (report["mode"], report["model_plan"]["model"]), "",
             "Analysis covers selected changes and retrieved evidence. It does not certify the whole repository.", "",
             "## Findings", ""]
    if not report["findings"]:
        lines.append("No discrepancies established within the checked evidence.")
    for finding in report["findings"]:
        lines += ["### %s (%s)" % (safe(finding["kind"]), safe(finding["confidence"])), "", safe(finding["summary"]), ""]
        for source in finding["evidence"]:
            location = source.get("url") or "%s:%s @ %s" % (source.get("path"), source.get("line"), source.get("sha"))
            lines += ["- %s — %s" % (safe(location), safe(source["quote"]))]
        for change in finding.get('code_changes', []):
            lines += ['', 'Responsible change in ' + safe(change['path']), '']
            lines.extend('    ' + line for line in change['patch'].splitlines())
        if finding.get("decision"):
            lines += ["", "Decision record: " + safe(json.dumps(finding["decision"]))]
        lines.append("")
    lines += ["## Coverage", "", "```json", json.dumps(report["coverage"], indent=2), "```", "", "## Warnings", ""]
    lines += ["- " + safe(warning) for warning in report["warnings"]] or ["None."]
    lines += ["", "## Errors", ""]
    lines += ["- " + safe(error) for error in report["errors"]] or ["None."]
    return "\n".join(lines) + "\n"


def write_report(directory, report):
    """Only local log output; no issue drafts or publication artifacts."""
    from datetime import datetime, timezone
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Keep a machine-readable latest snapshot as a log, and an append-only,
    # human-readable history with quotations, code locations and coverage.
    atomic_json(directory / 'analysis.log', report)
    with (directory / 'maintainer.log').open('a', encoding='utf-8') as stream:
        stream.write('\n=== %s ===\n' % datetime.now(timezone.utc).isoformat())
        if report.get('repository'):
            stream.write('Repository: %s\n' % safe(report['repository']))
        if report.get('event'):
            stream.write('Event: %s\n' % safe(report['event']))
        if report.get('pull_request'):
            stream.write('Pull request: #%s\n' % report['pull_request'])
        stream.write(markdown(report))
        stream.flush()
