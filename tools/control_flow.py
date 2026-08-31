"""Control-flow tools: how the agent records what it found and asks for help.

These are the tools that make the sweep the agent's own. It decides when a
result is worth archiving, when a control has been decided, and when a change
needs a human — none of that is sequenced for it in Python (PLAN §5.1).

The current run id is process-local rather than a tool argument, so the model
cannot accidentally write findings into a different run.
"""

from __future__ import annotations

import yaml
from strands import tool

from tools import approvals, notify, state
from tools.config import CATALOG_PATH

_current_run: dict[str, str] = {"run_id": ""}


def set_current_run(run_id: str) -> None:
    _current_run["run_id"] = run_id


def current_run() -> str:
    return _current_run["run_id"]


@tool
def get_control_catalog() -> dict:
    """Load the control catalog: the controls to assess and their pass conditions.

    Each control lists `candidate_tools`, which are SUGGESTIONS of where the
    evidence usually lives — not an execution order. Decide for yourself which
    tools to call, in what sequence, and when one result already answers
    another control. If a candidate tool fails, try a different route before
    giving up on the control.
    """
    with open(CATALOG_PATH) as f:
        catalog = yaml.safe_load(f)
    return {
        "framework": catalog.get("framework"),
        "control_count": len(catalog.get("controls", [])),
        "controls": catalog.get("controls", []),
    }


@tool
def save_evidence(tool_name: str, result: dict, control_id: str = "") -> dict:
    """Archive a tool result as citable evidence and get back an evidence_id.

    Call this for every tool result you actually use to decide a verdict. The
    raw JSON is archived to S3 and indexed in DynamoDB; the returned
    `evidence_id` is what you cite in `record_finding`, so a reader can trace
    any statement back to the exact observation behind it.
    """
    run_id = current_run()
    if not run_id:
        return {"error": "no active run"}
    rec = state.save_evidence_record(
        run_id=run_id, tool_name=tool_name, payload=result, control_id=control_id
    )
    state.append_audit(run_id, "save_evidence", {"tool": tool_name}, True, rec["evidence_id"])
    return rec


@tool
def record_finding(
    control_id: str,
    verdict: str,
    rationale: str,
    evidence_ids: list[str],
    remediation: str = "",
) -> dict:
    """Record your verdict for one control.

    Args:
        control_id: the catalog id, e.g. "ctrl-s3-encryption".
        verdict: exactly one of PASS, FAIL, PARTIAL, INDETERMINATE.
            Use INDETERMINATE when a tool error left you unable to observe the
            resource — never guess, and never report PASS for something you
            could not read.
        rationale: one line stating what you observed and why it decides the
            control. Reference concrete values from the evidence.
        evidence_ids: ids returned by `save_evidence` supporting this verdict.
            A verdict with no evidence is not acceptable.
        remediation: if fixable, what should change and by which tool.
    """
    run_id = current_run()
    if not run_id:
        return {"error": "no active run"}

    verdict = (verdict or "").strip().upper()
    if verdict not in state.VERDICTS:
        return {
            "error": f"invalid verdict {verdict!r}",
            "allowed": list(state.VERDICTS),
        }
    if not evidence_ids:
        return {
            "error": "a verdict must cite at least one evidence_id; "
            "call save_evidence on the tool result you used"
        }

    item = state.record_control(
        run_id=run_id,
        control_id=control_id,
        verdict=verdict,
        rationale=rationale,
        evidence_ids=evidence_ids,
        remediation=remediation,
    )
    state.append_audit(run_id, "record_finding", {"control": control_id}, True, verdict)
    return {"recorded": True, "control_id": control_id, "verdict": verdict,
            "evidence_ids": evidence_ids, "recorded_at": item["recorded_at"]}


@tool
def request_approval(
    action: str, resource: str, reason: str, control_id: str = ""
) -> dict:
    """Ask the human to approve a change. Returns a PENDING approval_id.

    Call this BEFORE any remediation tool. The remediation tool verifies the
    approval in code, so calling it without an APPROVED record will simply be
    refused — you cannot approve your own request.

    After calling this, do NOT wait or poll. Record the control's current
    (failing) verdict, note that approval was requested, and move on to the next
    control. The sweep is re-invoked with the outcome once the human decides.

    Args:
        action: the remediation tool name, e.g. "enable_s3_kms_encryption".
        resource: the exact resource the change targets, e.g. the bucket name.
        reason: why this change is needed, in plain language a founder can judge.
        control_id: the control this would remediate.
    """
    run_id = current_run()
    if not run_id:
        return {"error": "no active run"}

    allowed, guard_reason = approvals.guard_resource(resource)
    if not allowed:
        return {"status": "REFUSED", "message": guard_reason}

    record = approvals.create(
        run_id=run_id,
        action=action,
        resource_name=resource,
        reason=reason,
        control_id=control_id,
    )
    notify.notify_approval_request(record)
    state.append_audit(
        run_id, "request_approval", {"action": action, "resource": resource}, True,
        record["approval_id"],
    )
    return {
        "approval_id": record["approval_id"],
        "status": record["status"],
        "action": action,
        "resource": resource,
        "expires_at": record["expires_at_iso"],
        "next_step": (
            "Do not wait. Record the control's current verdict and continue the "
            "sweep. You will be re-invoked when the human decides."
        ),
    }


@tool
def get_approval_status(approval_id: str) -> dict:
    """Check whether a human has decided on an approval you requested."""
    record = approvals.get(approval_id)
    if not record:
        return {"error": f"approval {approval_id} not found"}
    return {
        "approval_id": approval_id,
        "status": record.get("status"),
        "action": record.get("action"),
        "resource": record.get("resource"),
        "decided_at": record.get("decided_at"),
        "expires_at": record.get("expires_at_iso"),
    }


@tool
def notify_user(subject: str, message: str) -> dict:
    """Email the account owner. Use sparingly: a run summary, or an urgent finding."""
    run_id = current_run()
    ok, detail = notify.send(subject, message)
    if run_id:
        state.append_audit(run_id, "notify_user", {"subject": subject}, ok, detail)
    return {"sent": ok, "detail": detail}


@tool
def get_previous_run_findings() -> dict:
    """Fetch the previous completed run's verdicts, to compare against this one.

    Use this to narrate drift: which controls regressed (PASS -> FAIL), which
    were fixed, and which are newly assessed. A regression is the most important
    thing in a run summary, so look for it explicitly.
    """
    run_id = current_run()
    prev = state.previous_run(run_id) if run_id else None
    if not prev:
        return {
            "previous_run": None,
            "note": "no previous completed run; this is the baseline.",
        }
    controls = state.get_controls(prev["run_id"])
    return {
        "previous_run": prev["run_id"],
        "finished_at": prev.get("finished_at"),
        "verdicts": {c["control_id"]: c["verdict"] for c in controls},
        "control_count": len(controls),
    }


@tool
def generate_trust_packet() -> dict:
    """Generate the auditor-ready trust packet for this run.

    Call this once, at the very end, after every control has a recorded verdict.
    The packet renders each verdict alongside the raw JSON evidence behind it,
    so a reader can verify any statement independently. A control with no cited
    evidence is rendered as a visible defect, so make sure every finding cites
    the evidence you used.
    """
    run_id = current_run()
    if not run_id:
        return {"error": "no active run"}
    from packet.render import generate

    out = generate(run_id, upload=True)
    out.pop("_html", None)
    out.pop("_json", None)
    state.append_audit(run_id, "generate_trust_packet", {}, True, str(out.get("counts")))
    return out
