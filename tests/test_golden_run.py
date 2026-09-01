"""Golden run: the whole sweep pipeline, with the model scripted.

What this proves: when the model asks for a tool, everything downstream is
sound — the tool executes, evidence reaches S3 and DynamoDB, the verdict is
recorded and validated, drift resolves against the previous run, the approval
gate holds, and the packet renders with citations.

What it does not prove: that the model *chooses* the right tools. That is the
product, and it stays UNVERIFIED until Bedrock access is granted. See
tests/fake_model.py.

Runs entirely against moto, so it needs no credentials and no Bedrock.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import boto3
import pytest

pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_model import ScriptedModel  # noqa: E402

REGION = "us-east-1"
BUCKET_OK = "attest-demo-good"
BUCKET_BAD = "attest-demo-logs"
BUCKET_DENIED = "attest-demo-denied"

TABLE_KEYS = {
    "attest_runs": ("run_id", None),
    "attest_controls": ("run_id", "control_id"),
    "attest_evidence": ("run_id", "evidence_id"),
    "attest_approvals": ("approval_id", None),
    "attest_audit_log": ("run_id", "seq"),
}


@pytest.fixture
def aws(monkeypatch):
    """A small fake account: one compliant bucket, one on SSE-S3, one unreadable."""
    for k, v in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": REGION,
        "AWS_REGION": REGION,
        "S3_BUCKET": "attest-evidence-test",
        "ATTEST_REDACT": "0",  # assert on real names; redaction has its own tests
    }.items():
        monkeypatch.setenv(k, v)

    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=REGION)
        for name, (hk, rk) in TABLE_KEYS.items():
            schema = [{"AttributeName": hk, "KeyType": "HASH"}]
            attrs = [{"AttributeName": hk, "AttributeType": "S"}]
            if rk:
                schema.append({"AttributeName": rk, "KeyType": "RANGE"})
                attrs.append({"AttributeName": rk, "AttributeType": "S"})
            ddb.create_table(
                TableName=name, KeySchema=schema, AttributeDefinitions=attrs,
                BillingMode="PAY_PER_REQUEST",
            )

        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket="attest-evidence-test")
        for b in (BUCKET_OK, BUCKET_BAD, BUCKET_DENIED):
            s3.create_bucket(Bucket=b)
        s3.put_bucket_encryption(
            Bucket=BUCKET_OK,
            ServerSideEncryptionConfiguration={
                "Rules": [{"ApplyServerSideEncryptionByDefault": {
                    "SSEAlgorithm": "aws:kms",
                    "KMSMasterKeyID": f"arn:aws:kms:{REGION}:123456789012:key/abc",
                }}]
            },
        )
        s3.put_bucket_encryption(
            Bucket=BUCKET_BAD,
            ServerSideEncryptionConfiguration={
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            },
        )

        from tools import config

        for c in (config.client, config.resource, config.account_id):
            c.cache_clear()
        for mod in ("tools.state", "tools.approvals", "tools.control_flow"):
            importlib.reload(importlib.import_module(mod))

        yield
        for c in (config.client, config.resource, config.account_id):
            c.cache_clear()


def _sweep_script(bucket: str) -> list[dict]:
    """A plausible sweep: look, archive, judge, ask, then summarise.

    The record_finding turn resolves its evidence id at call time, the way a
    real model would read it out of the save_evidence result it just saw.
    """

    def cite() -> dict:
        from tools import control_flow, state

        ev = state.get_evidence(control_flow.current_run())
        return {
            "control_id": "ctrl-s3-encryption",
            "verdict": "FAIL",
            "rationale": f"{bucket} uses SSE-S3, not a customer-managed KMS key.",
            "evidence_ids": [e["evidence_id"] for e in ev],
            "remediation": "enable_s3_kms_encryption",
        }

    return [
        {"tools": [{"name": "get_control_catalog", "input": {}}]},
        {"tools": [{"name": "get_previous_run_findings", "input": {}}]},
        {"tools": [{"name": "list_s3_encryption_status", "input": {}}]},
        {"tools": [{"name": "save_evidence", "input": {
            "tool_name": "list_s3_encryption_status",
            "result": {"probe": "recorded by the golden run"},
            "control_id": "ctrl-s3-encryption",
        }}]},
        {"tools": [{"name": "record_finding", "input": cite}]},
        {"tools": [{"name": "request_approval", "input": {
            "action": "enable_s3_kms_encryption",
            "resource": bucket,
            "reason": "Needs a customer-managed key.",
            "control_id": "ctrl-s3-encryption",
        }}]},
        {"text": "One control failing: a bucket without a customer-managed key. "
                 "I have asked for approval to fix it."},
    ]


def _run(script, run_id=None):
    """Drive a sweep with a scripted model. Returns (run_id, model, result)."""
    from agent.attest import ALL_TOOLS
    from agent.instructions import SYSTEM_PROMPT
    from strands import Agent
    from tools import control_flow, state

    run_id = run_id or state.new_run_id()
    state.start_run(run_id, trigger="golden", region=REGION)
    control_flow.set_current_run(run_id)

    model = ScriptedModel(script)
    agent = Agent(model=model, tools=ALL_TOOLS, system_prompt=SYSTEM_PROMPT)
    result = agent("Begin a compliance sweep.")
    state.finish_run(run_id, "COMPLETE", str(result))
    return run_id, model, result


# -- the golden run ----------------------------------------------------------


def test_sweep_runs_end_to_end(aws):
    from tools import state

    run_id, model, _ = _run(_sweep_script(BUCKET_BAD))

    run = state.get_run(run_id)
    assert run["status"] == "COMPLETE"
    assert run["summary"]

    # The agent must have been offered the whole toolset, not a subset.
    assert "list_s3_encryption_status" in model.seen_tool_specs
    assert "record_finding" in model.seen_tool_specs
    assert "enable_s3_kms_encryption" in model.seen_tool_specs
    assert len(model.seen_tool_specs) == 19


def test_evidence_is_archived_and_citable(aws):
    from tools import state
    from tools.config import evidence_bucket

    run_id, _, _ = _run(_sweep_script(BUCKET_BAD))

    evidence = state.get_evidence(run_id)
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev["s3_uri"].startswith("s3://")

    # The S3 object must actually exist and hold the tool output.
    key = ev["s3_uri"].split(f"{evidence_bucket()}/", 1)[1]
    body = boto3.client("s3", region_name=REGION).get_object(
        Bucket=evidence_bucket(), Key=key
    )["Body"].read().decode()
    assert "recorded by the golden run" in body


def test_verdict_is_recorded_with_its_citation(aws):
    from tools import state

    run_id, _, _ = _run(_sweep_script(BUCKET_BAD))

    controls = state.get_controls(run_id)
    assert len(controls) == 1
    c = controls[0]
    assert c["control_id"] == "ctrl-s3-encryption"
    assert c["verdict"] == "FAIL"
    assert c["evidence_ids"], "a recorded verdict must cite evidence"


def test_every_tool_call_reaches_the_audit_log(aws):
    from tools import state

    run_id, _, _ = _run(_sweep_script(BUCKET_BAD))

    tools_logged = {a["tool"] for a in state.get_audit(run_id)}
    assert {"save_evidence", "record_finding", "request_approval"} <= tools_logged


def test_approval_is_created_pending_and_not_applied(aws):
    """The agent asks; it must not be able to grant its own request."""
    from tools import approvals

    run_id, _, _ = _run(_sweep_script(BUCKET_BAD))

    pending = approvals.list_pending(run_id=run_id)
    assert len(pending) == 1
    a = pending[0]
    assert a["status"] == "PENDING"
    assert a["resource"] == BUCKET_BAD
    assert a["action"] == "enable_s3_kms_encryption"


def test_remediation_is_refused_while_the_approval_is_pending(aws):
    """The whole point: an agent that asked cannot proceed until a human decides."""
    from tools import approvals
    from tools.remediation.s3 import enable_s3_kms_encryption

    run_id, _, _ = _run(_sweep_script(BUCKET_BAD))
    aid = approvals.list_pending(run_id=run_id)[0]["approval_id"]

    out = enable_s3_kms_encryption.__wrapped__(
        bucket=BUCKET_BAD, approval_id=aid, kms_key="alias/attest-demo"
    )
    assert out["status"] == "AWAITING_APPROVAL"

    s3 = boto3.client("s3", region_name=REGION)
    rule = s3.get_bucket_encryption(Bucket=BUCKET_BAD)[
        "ServerSideEncryptionConfiguration"]["Rules"][0][
        "ApplyServerSideEncryptionByDefault"]
    assert rule["SSEAlgorithm"] == "AES256", "bucket changed despite no approval"


def test_approval_then_remediation_flips_the_control(aws):
    """The demo moment, with the model scripted: approve, apply, verify, re-judge."""
    from tools import approvals, control_flow, state
    from tools.evidence.s3 import list_s3_encryption_status
    from tools.remediation.s3 import enable_s3_kms_encryption

    run_id, _, _ = _run(_sweep_script(BUCKET_BAD))
    aid = approvals.list_pending(run_id=run_id)[0]["approval_id"]

    approvals.decide(aid, approved=True, decided_by="test")
    out = enable_s3_kms_encryption.__wrapped__(
        bucket=BUCKET_BAD, approval_id=aid,
        kms_key=f"arn:aws:kms:{REGION}:123456789012:key/abc",
    )
    assert out["status"] == "APPLIED"
    assert out["verified"] is True
    assert out["before"]["algorithm"] == "AES256"
    assert out["after"]["algorithm"] == "aws:kms"

    # Re-observing must now show the control satisfied.
    after = list_s3_encryption_status.__wrapped__()
    bad = [b for b in after["buckets"] if b["bucket"] == BUCKET_BAD][0]
    assert bad["meets_kms_requirement"] is True

    # And the approval is burnt, so it cannot authorise a second write.
    again = enable_s3_kms_encryption.__wrapped__(
        bucket=BUCKET_BAD, approval_id=aid, kms_key="alias/x"
    )
    assert again["status"] == "AWAITING_APPROVAL"
    assert "already used" in again["message"]

    control_flow.set_current_run(run_id)
    assert state.get_run(run_id)


def test_second_run_sees_the_first_as_its_drift_baseline(aws):
    """Drift needs a previous COMPLETE run to compare against."""
    from tools import control_flow, state

    first, _, _ = _run(_sweep_script(BUCKET_BAD))

    second = state.new_run_id()
    state.start_run(second, trigger="golden-2", region=REGION)
    control_flow.set_current_run(second)

    prev = control_flow.get_previous_run_findings.__wrapped__()
    assert prev["previous_run"] == first
    assert prev["verdicts"]["ctrl-s3-encryption"] == "FAIL"


def test_packet_renders_with_citations(aws):
    from packet.render import build_packet, generate

    run_id, _, _ = _run(_sweep_script(BUCKET_BAD))

    packet = build_packet(run_id)
    assert packet["counts"]["FAIL"] == 1
    row = packet["controls"][0]
    assert row["uncited"] is False
    assert row["evidence"], "the packet must carry the evidence, not just its id"
    # Controls the run never assessed are named, not silently dropped.
    assert len(packet["not_assessed"]) == 9

    out = generate(run_id, upload=True)
    assert out["uncited_controls"] == []
    assert "<!doctype html>" in out["_html"].lower()


def test_a_tool_failure_does_not_abort_the_sweep(aws, monkeypatch):
    """A sweep that stops at the first error is useless; it must carry on."""
    from tools import state
    from tools.evidence import s3 as s3mod

    def explode(*a, **k):
        raise RuntimeError("simulated AWS outage")

    monkeypatch.setattr(s3mod, "client", explode)

    def cite_failure() -> dict:
        from tools import control_flow, state

        ev = state.get_evidence(control_flow.current_run())
        return {
            "control_id": "ctrl-s3-encryption",
            "verdict": "INDETERMINATE",
            "rationale": "The tool failed, so the bucket state is unknown.",
            "evidence_ids": [e["evidence_id"] for e in ev],
        }

    script = [
        {"tools": [{"name": "list_s3_encryption_status", "input": {}}]},
        {"tools": [{"name": "save_evidence", "input": {
            "tool_name": "list_s3_encryption_status",
            "result": {"error": "simulated AWS outage"},
            "control_id": "ctrl-s3-encryption",
        }}]},
        {"tools": [{"name": "record_finding", "input": cite_failure}]},
        {"text": "One control could not be assessed."},
    ]
    run_id, _, _ = _run(script)

    assert state.get_run(run_id)["status"] == "COMPLETE"
    controls = state.get_controls(run_id)
    assert controls[0]["verdict"] == "INDETERMINATE"


def test_the_system_prompt_reaches_the_model(aws):
    """The verdict contract lives in the prompt; it must actually be sent."""
    _, model, _ = _run(_sweep_script(BUCKET_BAD))

    assert model.seen_system_prompt
    for phrase in ("INDETERMINATE", "an error is not a pass", "request_approval"):
        assert phrase.lower() in model.seen_system_prompt.lower()


def test_a_fabricated_citation_is_rejected(aws):
    """A citation naming evidence that does not exist reads as substantiated in
    the packet while pointing at nothing. It must be refused at record time.

    Found by the golden run: record_finding checked that evidence_ids was
    non-empty but never that the ids were real.
    """
    from tools import control_flow, state

    run_id = state.new_run_id()
    state.start_run(run_id, trigger="golden", region=REGION)
    control_flow.set_current_run(run_id)

    out = control_flow.record_finding.__wrapped__(
        control_id="ctrl-s3-encryption",
        verdict="PASS",
        rationale="Everything is fine.",
        evidence_ids=["ev-doesnotexist"],
    )
    assert "error" in out
    assert "unknown evidence_id" in out["error"]
    assert state.get_controls(run_id) == [], "the verdict must not be stored"


def test_a_partly_fabricated_citation_is_rejected(aws):
    """One real id does not launder a fabricated one alongside it."""
    from tools import control_flow, state

    run_id = state.new_run_id()
    state.start_run(run_id, trigger="golden", region=REGION)
    control_flow.set_current_run(run_id)

    real = control_flow.save_evidence.__wrapped__(
        tool_name="list_s3_encryption_status", result={"x": 1},
        control_id="ctrl-s3-encryption",
    )["evidence_id"]

    out = control_flow.record_finding.__wrapped__(
        control_id="ctrl-s3-encryption", verdict="PASS", rationale="r",
        evidence_ids=[real, "ev-invented"],
    )
    assert "error" in out and "ev-invented" in out["error"]
    assert state.get_controls(run_id) == []


def test_evidence_from_another_run_cannot_be_cited(aws):
    """Citations must be scoped to the run that produced them, or a packet could
    cite observations made at a different time against different state."""
    from tools import control_flow, state

    first = state.new_run_id()
    state.start_run(first, trigger="golden", region=REGION)
    control_flow.set_current_run(first)
    stolen = control_flow.save_evidence.__wrapped__(
        tool_name="list_s3_encryption_status", result={"x": 1},
    )["evidence_id"]

    second = state.new_run_id()
    state.start_run(second, trigger="golden", region=REGION)
    control_flow.set_current_run(second)

    out = control_flow.record_finding.__wrapped__(
        control_id="ctrl-s3-encryption", verdict="PASS", rationale="r",
        evidence_ids=[stolen],
    )
    assert "error" in out and "unknown evidence_id" in out["error"]


# -- approve and resume: the demo moment -------------------------------------


def test_resume_after_approval_applies_verifies_and_reverdicts(aws, monkeypatch):
    """The full demo path with the model scripted.

    The agent asks, a human approves, the agent is re-invoked, applies the
    change, re-reads the resource, and records a fresh PASS citing new evidence.
    Until now only the halves either side of the model call were verified.
    """
    from agent import attest as agent_mod
    from strands import Agent
    from tools import approvals, control_flow, state

    run_id, _, _ = _run(_sweep_script(BUCKET_BAD))
    aid = approvals.list_pending(run_id=run_id)[0]["approval_id"]

    # The human decides. Nothing before this point may change the bucket.
    approvals.decide(aid, approved=True, decided_by="test")

    def apply_and_verify() -> dict:
        return {
            "bucket": BUCKET_BAD,
            "approval_id": aid,
            "kms_key": f"arn:aws:kms:{REGION}:123456789012:key/abc",
        }

    def recheck() -> dict:
        from tools import control_flow as cf

        ev = state.get_evidence(cf.current_run())
        return {
            "tool_name": "list_s3_encryption_status",
            "result": {"post_change": "re-read after remediation"},
            "control_id": "ctrl-s3-encryption",
        }

    def new_verdict() -> dict:
        from tools import control_flow as cf

        ev = state.get_evidence(cf.current_run())
        return {
            "control_id": "ctrl-s3-encryption",
            "verdict": "PASS",
            "rationale": f"{BUCKET_BAD} now uses SSE-KMS with a customer-managed key.",
            # Cite the newest evidence, gathered after the change.
            "evidence_ids": [sorted(e["evidence_id"] for e in ev)[-1]],
        }

    resume_script = [
        {"tools": [{"name": "enable_s3_kms_encryption", "input": apply_and_verify}]},
        {"tools": [{"name": "list_s3_encryption_status", "input": {}}]},
        {"tools": [{"name": "save_evidence", "input": recheck}]},
        {"tools": [{"name": "record_finding", "input": new_verdict}]},
        {"text": "Applied and verified. The bucket now uses a customer-managed key."},
    ]

    control_flow.set_current_run(run_id)
    model = ScriptedModel(resume_script)
    from agent.attest import ALL_TOOLS
    from agent.instructions import SYSTEM_PROMPT

    agent = Agent(model=model, tools=ALL_TOOLS, system_prompt=SYSTEM_PROMPT)
    agent_mod.resume_after_decision(run_id, aid, agent=agent)

    # The bucket really changed.
    rule = boto3.client("s3", region_name=REGION).get_bucket_encryption(
        Bucket=BUCKET_BAD
    )["ServerSideEncryptionConfiguration"]["Rules"][0][
        "ApplyServerSideEncryptionByDefault"]
    assert rule["SSEAlgorithm"] == "aws:kms"

    # The control flipped, and the new verdict cites evidence gathered after
    # the change rather than reusing the pre-change observation.
    control = state.get_controls(run_id)[0]
    assert control["verdict"] == "PASS"
    evidence = {e["evidence_id"]: e for e in state.get_evidence(run_id)}
    cited = evidence[control["evidence_ids"][0]]
    assert "post_change" in cited["result_json"]

    # The approval is burnt.
    assert approvals.get(aid)["status"] == approvals.APPLIED


def test_resume_after_rejection_leaves_the_control_failing(aws):
    """A declined fix must be recorded as declined, not quietly retried."""
    from agent import attest as agent_mod
    from agent.attest import ALL_TOOLS
    from agent.instructions import SYSTEM_PROMPT
    from strands import Agent
    from tools import approvals, control_flow, state

    run_id, _, _ = _run(_sweep_script(BUCKET_BAD))
    aid = approvals.list_pending(run_id=run_id)[0]["approval_id"]
    approvals.decide(aid, approved=False, decided_by="test")

    # A model that tries the write anyway must be refused by the gate.
    script = [
        {"tools": [{"name": "enable_s3_kms_encryption", "input": {
            "bucket": BUCKET_BAD, "approval_id": aid, "kms_key": "alias/x",
        }}]},
        {"text": "The fix was declined, so the control stays failing."},
    ]
    control_flow.set_current_run(run_id)
    agent = Agent(model=ScriptedModel(script), tools=ALL_TOOLS,
                  system_prompt=SYSTEM_PROMPT)
    agent_mod.resume_after_decision(run_id, aid, agent=agent)

    rule = boto3.client("s3", region_name=REGION).get_bucket_encryption(
        Bucket=BUCKET_BAD
    )["ServerSideEncryptionConfiguration"]["Rules"][0][
        "ApplyServerSideEncryptionByDefault"]
    assert rule["SSEAlgorithm"] == "AES256", "bucket changed despite rejection"
    assert state.get_controls(run_id)[0]["verdict"] == "FAIL"
