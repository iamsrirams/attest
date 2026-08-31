"""Attest CLI.

    python scripts/attest_cli.py sweep
    python scripts/attest_cli.py pending
    python scripts/attest_cli.py approve <approval_id>
    python scripts/attest_cli.py reject  <approval_id>
    python scripts/attest_cli.py show    <run_id>
    python scripts/attest_cli.py resolve <pseudonym>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import approvals, state  # noqa: E402

VERDICT_MARK = {
    "PASS": "\033[32mPASS\033[0m",
    "FAIL": "\033[31mFAIL\033[0m",
    "PARTIAL": "\033[33mPARTIAL\033[0m",
    "INDETERMINATE": "\033[90mINDETERMINATE\033[0m",
}


def cmd_sweep(args) -> int:
    from agent.attest import run_sweep

    run_id, _, result = run_sweep(trigger=args.trigger)
    print(f"\n{'=' * 72}\nrun {run_id}\n{'=' * 72}")
    print(result)
    _print_controls(run_id)
    return 0


def _print_controls(run_id: str) -> None:
    controls = state.get_controls(run_id)
    if not controls:
        print("\n(no controls recorded)")
        return
    print(f"\n{len(controls)} controls recorded:\n")
    for c in sorted(controls, key=lambda x: x["control_id"]):
        mark = VERDICT_MARK.get(c["verdict"], c["verdict"])
        print(f"  {mark:<24} {c['control_id']}")
        print(f"  {'':<15} {c['rationale'][:100]}")
        print(f"  {'':<15} evidence: {', '.join(c.get('evidence_ids', [])) or '-'}\n")


def cmd_pending(args) -> int:
    items = approvals.list_pending()
    if not items:
        print("no pending approvals")
        return 0
    for a in items:
        print(f"\n  {a['approval_id']}  [{a['status']}]")
        print(f"    action:   {a['action']}")
        print(f"    resource: {a['resource']}")
        print(f"    reason:   {a['reason'][:160]}")
        print(f"    expires:  {a.get('expires_at_iso')}")
    print(f"\napprove with: python scripts/attest_cli.py approve <approval_id>")
    return 0


def _decide(approval_id: str, approved: bool) -> int:
    record = approvals.decide(approval_id, approved=approved, decided_by="cli")
    if not record:
        print(f"approval {approval_id} not found")
        return 1
    print(f"{approval_id} -> {record['status']}")

    if not approved:
        return 0

    print("\nresuming the agent to apply and verify...\n")
    from agent.attest import resume_after_decision

    result = resume_after_decision(record["run_id"], approval_id)
    print(result)
    return 0


def cmd_approve(args) -> int:
    return _decide(args.approval_id, True)


def cmd_reject(args) -> int:
    return _decide(args.approval_id, False)


def cmd_show(args) -> int:
    run = state.get_run(args.run_id)
    if not run:
        print(f"run {args.run_id} not found")
        return 1
    print(f"run     {run['run_id']}")
    print(f"status  {run['status']}")
    print(f"started {run['started_at']}   finished {run.get('finished_at') or '-'}")
    _print_controls(args.run_id)
    if run.get("summary"):
        print(f"\nsummary:\n{run['summary']}")
    return 0


def cmd_runs(args) -> int:
    for r in state.list_runs():
        print(f"  {r['run_id']}  {r['status']:<9} {r['started_at']}")
    return 0


def cmd_resolve(args) -> int:
    """Owner-side lookup: turn a pseudonym back into the real identity."""
    from tools.redact import default_redactor

    real = default_redactor().resolve(args.pseudonym)
    print(real if real else f"no mapping for {args.pseudonym!r}")
    return 0 if real else 1


def main() -> int:
    p = argparse.ArgumentParser(prog="attest", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sweep", help="run a full compliance sweep")
    s.add_argument("--trigger", default="manual")
    s.set_defaults(fn=cmd_sweep)

    s = sub.add_parser("pending", help="list approvals awaiting a decision")
    s.set_defaults(fn=cmd_pending)

    s = sub.add_parser("approve", help="approve a change, then apply and verify it")
    s.add_argument("approval_id")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("reject", help="reject a change")
    s.add_argument("approval_id")
    s.set_defaults(fn=cmd_reject)

    s = sub.add_parser("show", help="show a run's verdicts")
    s.add_argument("run_id")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("runs", help="list recent runs")
    s.set_defaults(fn=cmd_runs)

    s = sub.add_parser("resolve", help="resolve a pseudonym to the real identity")
    s.add_argument("pseudonym")
    s.set_defaults(fn=cmd_resolve)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
