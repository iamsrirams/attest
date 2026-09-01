"""Lambda entry points.

The agent itself knows nothing about Lambda — this module is the only thing
that does, which is what keeps the AgentCore-vs-Lambda choice a deployment
decision rather than a rewrite (PLAN §3).

Two handlers:
  sweep   — EventBridge Scheduler target, and manual invoke
  resume  — called after a human decides an approval
"""

from __future__ import annotations

import json
from typing import Any

from tools import state


def sweep(event: dict | None = None, context: Any = None) -> dict:
    """Run a full sweep. Wired to the nightly schedule."""
    from agent.attest import run_sweep

    trigger = (event or {}).get("trigger", "lambda")
    run_id = state.new_run_id()

    try:
        run_id, _, result = run_sweep(trigger=trigger, run_id=run_id)
        controls = state.get_controls(run_id)
        counts: dict[str, int] = {}
        for c in controls:
            counts[c["verdict"]] = counts.get(c["verdict"], 0) + 1
        return {
            "statusCode": 200,
            "body": json.dumps(
                {"run_id": run_id, "counts": counts, "summary": str(result)[:4000]}
            ),
        }
    except Exception as e:  # noqa: BLE001
        # A failed sweep still has to leave a record; a scheduled job that dies
        # silently is worse than one that reports the failure.
        state.finish_run(run_id, "FAILED", f"{type(e).__name__}: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"run_id": run_id, "error": f"{type(e).__name__}: {e}"}),
        }


def resume(event: dict | None = None, context: Any = None) -> dict:
    """Resume a sweep after a human decides an approval.

    The decision is read from DynamoDB, never taken from the event, so an
    invoke cannot assert that something was approved.
    """
    from agent.attest import resume_after_decision

    event = event or {}
    approval_id = event.get("approval_id")
    run_id = event.get("run_id")
    if not approval_id or not run_id:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "approval_id and run_id are required"}),
        }

    try:
        result = resume_after_decision(run_id, approval_id)
        return {
            "statusCode": 200,
            "body": json.dumps({"run_id": run_id, "result": str(result)[:4000]}),
        }
    except Exception as e:  # noqa: BLE001
        state.append_audit(
            run_id, "resume_after_decision", {"approval_id": approval_id}, False,
            f"{type(e).__name__}: {e}",
        )
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"{type(e).__name__}: {e}"}),
        }


def api(event: dict | None = None, context: Any = None) -> dict:
    """API Gateway proxy handler, wrapping the same FastAPI app.

    Imported lazily so the sweep handler does not pay for mangum or FastAPI on
    a cold start it does not need.
    """
    from mangum import Mangum

    from api.app import app

    return Mangum(app, lifespan="off")(event, context)
