import argparse
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path

from .engine import analyse
from .hardware import Hardware, detect_hardware, select_model
from .history import read_json, validate_history
from .llm import Ollama
from .report import atomic_json, write_report
from .repository import Repository


def hardware_options(parser):
    parser.add_argument("--config", help="Local model configuration saved by bootstrap.py")
    parser.add_argument("--ram-gb", type=float, help="Override RAM GiB (also enables simulation without probing)")
    parser.add_argument("--vram-gb", type=float, help="Dedicated GPU GiB; never add this to system RAM")
    parser.add_argument("--unified", action="store_true", help="With --ram-gb, simulate an Apple Silicon Mac")
    parser.add_argument("--model", help="Explicit locally installed Ollama tag; memory fit is your responsibility")
    parser.add_argument("--context", type=int, help="Context override, bounded by selected hardware tier")


def plan_for(args):
    if getattr(args, 'no_llm', False) and args.ram_gb is None and args.vram_gb is None:
        return {'hardware': None, 'tier': 'static_only', 'model': 'disabled', 'context': 0,
                'quantization': 'none', 'budget_gb': 0, 'override': False}
    if args.ram_gb is not None:
        hw = Hardware("override", "arm64" if args.unified else "override", args.ram_gb, args.unified, args.vram_gb)
    else:
        hw = detect_hardware()
        if args.vram_gb is not None:
            hw.vram_gb = args.vram_gb
    if getattr(args, "no_llm", False):
        if not math.isfinite(hw.ram_gb) or hw.ram_gb <= 0 or hw.vram_gb is not None and (not math.isfinite(hw.vram_gb) or hw.vram_gb <= 0):
            raise ValueError("Memory capacities must be positive finite numbers.")
        return {"hardware": asdict(hw), "tier": "static_only", "model": "disabled",
                "context": 0, "quantization": "none", "budget_gb": 0, "override": False}
    return select_model(hw, args.model, args.context)


def audit(args, repo, base, plan):
    state_path = Path(args.state) if args.state else None
    identity = str(repo.path)
    history_data = validate_history(read_json(args.history_json)) if args.history_json else None
    reviewer = None
    if not args.no_llm and base != repo.head:
        reviewer = Ollama(plan, args.ollama_url)
        reviewer.ready()
    report = analyse(repo, base, plan, reviewer, history_data, args.max_files, args.max_docs, args.max_packets, args.include_gaps)
    if history_data:
        report["history_repository"] = history_data.get("repository", "user-supplied")
    write_report(args.out, report)
    # A completed scan is not a claim of correctness. Skipped work/errors must not
    # be silently bypassed in the next run. Clipped semantic evidence also blocks advancement.
    clipped = any("clipped:" in w for w in report["warnings"])
    if state_path and not report["errors"] and not report["coverage"]["skipped"] and not clipped:
        atomic_json(state_path, {"repository_path": identity, "head": repo.head})
    print("%s: %s findings; %s errors. Report: %s" %
          (report["status"], len(report["findings"]), len(report["errors"]), Path(args.out).resolve() / "maintainer.log"))
    if report["errors"] or report["coverage"]["skipped"] or clipped:
        return 2
    return 1 if any(f["kind"] in ("documentation_drift", "documentation_gap", "decision_conflict") for f in report["findings"]) else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline local-model documentation/decision audit of main-to-main changes")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="Detect hardware and print a local model plan; no network")
    hardware_options(doctor)
    check = sub.add_parser('check', help='Check dependencies and installed local model on this laptop')
    hardware_options(check)
    check.add_argument('--ollama-url', default='http://127.0.0.1:11434')
    github = sub.add_parser('github-watch', help='Read GitHub PRs using local AI and write local maintainer logs')
    github.add_argument('--repo', required=True, help='GitHub OWNER/REPO')
    github.add_argument('--state', required=True)
    github.add_argument('--out', default='.mainline/github')
    github.add_argument('--interval', type=float, default=60)
    github.add_argument('--once', action='store_true')
    github.add_argument('--rescan-initial', action='store_true', help='Repeat the first-run repository audit')
    github.add_argument('--no-llm', action='store_true')
    github.add_argument('--ollama-url', default='http://127.0.0.1:11434')
    github.add_argument('--max-files', type=int, default=30)
    github.add_argument('--max-docs', type=int, default=200)
    github.add_argument('--max-packets', type=int, default=40)
    hardware_options(github)
    for command in ("scan", "watch", "audit-local"):
        scan = sub.add_parser(command, help="Audit main once" if command == "scan" else "Watch local main for post-merge updates")
        scan.add_argument("--path", required=True, help="Complete local Git repository or bare copy")
        scan.add_argument("--at-main", help="Replay a historical main SHA without reading future documentation")
        scan.add_argument("--base", help="Previous main commit SHA; default: saved baseline or first parent")
        scan.add_argument("--state", help="Optional baseline JSON; updated only when analysis has no errors or skips")
        scan.add_argument("--history-json", help="Local exported discussions; never fetched")
        scan.add_argument("--out", default=".mainline/report")
        scan.add_argument("--include-gaps", action="store_true", help="Include speculative missing-documentation review hints")
        scan.add_argument("--no-llm", action="store_true", help="Only deterministic Python example/signature checks")
        scan.add_argument("--ollama-url", default="http://127.0.0.1:11434")
        scan.add_argument("--max-files", type=int, default=30)
        scan.add_argument("--max-docs", type=int, default=200)
        scan.add_argument("--max-packets", type=int, default=40)
        hardware_options(scan)
        if command == "audit-local":
            scan.add_argument("--max-sections", type=int, help="Stop and save a partial report after this many documentation sections")
            scan.add_argument("--include", action="append", help="Tracked file or directory prefix; repeat to scope the audit")
        if command == "watch":
            scan.add_argument("--interval", type=float, default=2.0)
            scan.add_argument("--once", action="store_true", help="Check main once and exit; useful in a post-merge hook")
    args = parser.parse_args(argv)
    try:
        if args.config:
            settings = read_json(args.config)
            if not isinstance(settings, dict) or settings.get('version') != 1:
                raise ValueError('Unsupported model configuration. Run bootstrap.py again.')
            if not isinstance(settings.get('model'), str) or not isinstance(settings.get('context'), int) or not isinstance(settings.get('ollama_url'), str):
                raise ValueError('Configuration requires model, context and ollama_url.')
            explicit = list(argv if argv is not None else sys.argv[1:])
            for name in ('model', 'context', 'ollama_url'):
                option = '--' + name.replace('_', '-')
                if not any(token == option or token.startswith(option + '=') for token in explicit):
                    setattr(args, name, settings[name])
            Ollama({}, settings['ollama_url'])  # Validate loopback without contacting the server.

        if args.command == 'github-watch':
            from .github import GitHub
            from .monitor import PRMonitor
            from .initial import audit_repository
            plan = plan_for(args)
            reviewer = None
            if not args.no_llm:
                reviewer = Ollama(plan, args.ollama_url)
                reviewer.ready()
                reviewer.keep_alive = '5m'
            monitor = PRMonitor(GitHub(args.repo),
                lambda snapshot, base: analyse(snapshot, base, plan, reviewer, max_files=args.max_files,
                                              max_docs=args.max_docs, max_packets=args.max_packets),
                args.state, args.out,
                analyse_initial=lambda snapshot, base: audit_repository(snapshot, base, plan, reviewer))
            if args.rescan_initial:
                monitor.state.pop('initial_audit', None)
            return monitor.run(args.interval, args.once)
        if args.command == 'audit-local':
            from .initial import ScopedLocalSnapshot, EMPTY, audit_repository
            plan = plan_for(args)
            reviewer = None
            if not args.no_llm:
                reviewer = Ollama(plan, args.ollama_url)
                reviewer.ready()
                reviewer.keep_alive = '5m'
            repository = Repository(args.path)
            snapshot = ScopedLocalSnapshot(repository, args.include)
            if not snapshot.files(snapshot.head):
                raise ValueError('No tracked files matched the requested audit scope.')
            def checkpoint(current):
                import copy
                partial = copy.deepcopy(current)
                partial['status'] = 'running_partial'
                partial['coverage']['partial'] = True
                partial['coverage']['skipped'].append('Audit is still running.')
                write_report(args.out, partial)
            report = audit_repository(snapshot, EMPTY, plan, reviewer,
                                      max_sections=args.max_sections, progress=checkpoint)
            report['coverage']['included_paths'] = args.include or ['all tracked files']
            write_report(args.out, report)
            print('Local audit: %s findings, %s errors. Report: %s' % (len(report['findings']), len(report['errors']), Path(args.out).resolve() / 'maintainer.log'))
            if report['errors'] or report['coverage']['skipped']:
                return 2
            return 1 if report['findings'] else 0
        if args.command == "watch":
            from .watch import watch_main
            if not args.state:
                raise ValueError("watch requires --state to retain its main baseline.")
            if args.at_main:
                raise ValueError("Use scan --at-main for historical replay.")
            if args.base:
                raise ValueError('Use scan --base for a manual comparison. Watch uses its saved baseline.')
            return watch_main(args.path, args.state,
                              lambda repo, base: audit(args, repo, base, plan_for(args)),
                              args.interval, 1 if args.once else None)
        if getattr(args, "at_main", None) and args.state:
            raise ValueError('Historical replay requires a separate report without --state.')
        plan = plan_for(args)
        if args.command == 'check':
            from .diagnostics import check_runtime
            result = check_runtime(plan, args.ollama_url)
            print(json.dumps(result, indent=2))
            return 0 if result['ready'] else 2
        if args.command == "doctor":
            print(json.dumps(plan, indent=2))
            return 0
        repo = Repository(args.path, at=args.at_main)
        identity = str(repo.path)
        requested = args.base
        state_path = Path(args.state) if args.state else None
        if not requested and state_path and state_path.exists():
            state = read_json(state_path)
            if not isinstance(state, dict) or not isinstance(state.get("head"), str):
                raise ValueError("Saved baseline must contain a string head SHA.")
            if state.get("repository_path") != identity:
                raise ValueError("Saved baseline belongs to a different repository path.")
            requested = state["head"]
        base = repo.baseline(requested)
        return audit(args, repo, base, plan)
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, KeyError) as exc:
        print("Error: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
