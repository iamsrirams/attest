"""Failure scenarios from PLAN §8 Phase 7.

Each of these is a way the system could quietly produce a wrong answer rather
than an obvious error, which is the failure mode that matters for a compliance
tool. A crash gets noticed; a false PASS does not.
"""

from __future__ import annotations

import importlib

import boto3
import pytest
from botocore.exceptions import ClientError

pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402


def _client_error(code: str, op: str = "GetBucketEncryption") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


# -- AccessDenied must become INDETERMINATE, never PASS -----------------------


def test_unreadable_bucket_is_not_reported_as_compliant(monkeypatch):
    """The seeded demo case. A bucket we cannot read is unknown, not compliant.

    Reporting PASS here would be a false assurance an auditor would eventually
    catch, which is the worst outcome this tool can produce.
    """
    from tools.evidence import s3 as s3mod

    class FakeS3:
        def list_buckets(self):
            return {"Buckets": [{"Name": "readable"}, {"Name": "denied"}]}

        def get_bucket_encryption(self, Bucket):
            if Bucket == "denied":
                raise _client_error("AccessDenied")
            return {
                "ServerSideEncryptionConfiguration": {
                    "Rules": [
                        {
                            "ApplyServerSideEncryptionByDefault": {
                                "SSEAlgorithm": "aws:kms",
                                "KMSMasterKeyID": "arn:aws:kms:us-east-1:1:key/abc",
                            }
                        }
                    ]
                }
            }

    monkeypatch.setattr(s3mod, "client", lambda svc: FakeS3())
    monkeypatch.setenv("ATTEST_REDACT", "0")

    out = s3mod.list_s3_encryption_status.__wrapped__()

    denied = [b for b in out["buckets"] if b["bucket"] == "denied"][0]
    assert denied["meets_kms_requirement"] is None, "unreadable must be null, not False"
    assert denied["error"] == "AccessDenied"
    assert out["unreadable_count"] == 1
    # It must not appear as a failure either — we did not observe non-compliance.
    assert "denied" not in out["not_meeting_kms_requirement"]


def test_a_missing_config_is_a_real_failure_not_an_error(monkeypatch):
    """The distinction the agent depends on: 'no encryption rule' is an
    observation; any other error code is a blind spot."""
    from tools.evidence import s3 as s3mod

    class FakeS3:
        def list_buckets(self):
            return {"Buckets": [{"Name": "plain"}]}

        def get_bucket_encryption(self, Bucket):
            raise _client_error(s3mod.NO_ENCRYPTION_CODE)

    monkeypatch.setattr(s3mod, "client", lambda svc: FakeS3())
    monkeypatch.setenv("ATTEST_REDACT", "0")

    out = s3mod.list_s3_encryption_status.__wrapped__()
    assert out["buckets"][0]["meets_kms_requirement"] is False
    assert out["unreadable_count"] == 0


def test_tool_returns_error_as_data_when_the_whole_call_fails(monkeypatch):
    """A sweep must survive a tool failing outright."""
    from tools.evidence import s3 as s3mod

    class FakeS3:
        def list_buckets(self):
            raise _client_error("AccessDenied", "ListBuckets")

    monkeypatch.setattr(s3mod, "client", lambda svc: FakeS3())
    monkeypatch.setenv("ATTEST_REDACT", "0")

    out = s3mod.list_s3_encryption_status.__wrapped__()
    assert out["error"] == "AccessDenied"  # returned, not raised


def test_an_aws_managed_key_does_not_satisfy_the_control():
    """aws/s3 is encryption, but not customer-controlled encryption."""
    from tools.evidence.s3 import _is_customer_managed

    assert _is_customer_managed("arn:aws:kms:us-east-1:1:key/abc") is True
    assert _is_customer_managed("alias/aws/s3") is False
    assert _is_customer_managed(None) is False


# -- a rejected approval must not retry ---------------------------------------


@pytest.fixture
def approvals_mod(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    with mock_aws():
        boto3.client("dynamodb", region_name="us-east-1").create_table(
            TableName="attest_approvals",
            KeySchema=[{"AttributeName": "approval_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "approval_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        from tools import config

        config.client.cache_clear()
        config.resource.cache_clear()
        from tools import approvals

        importlib.reload(approvals)
        yield approvals
        config.client.cache_clear()
        config.resource.cache_clear()


def test_rejection_is_terminal(approvals_mod):
    """A rejected change stays rejected. The agent is told not to re-request it,
    but the record must not permit a retry even if it does."""
    a = approvals_mod.create("run-1", "enable_s3_kms_encryption", "attest-demo-x", "why")
    approvals_mod.decide(a["approval_id"], approved=False)

    for _ in range(3):
        ok, why = approvals_mod.check(
            a["approval_id"], "enable_s3_kms_encryption", "attest-demo-x"
        )
        assert ok is False and "REJECTED" in why


def test_resume_refuses_an_undecided_approval(approvals_mod, monkeypatch):
    """Resuming on a PENDING approval would mean acting without a decision."""
    from agent import attest as agent_mod

    a = approvals_mod.create("run-1", "enable_s3_kms_encryption", "attest-demo-x", "why")
    monkeypatch.setattr(agent_mod, "approvals", approvals_mod)

    with pytest.raises(ValueError, match="PENDING"):
        agent_mod.resume_after_decision("run-1", a["approval_id"])


def test_resume_reads_the_decision_from_the_record(approvals_mod, monkeypatch):
    """The caller cannot assert a decision; only the stored record decides.

    This is what stops a crafted Lambda event or API call from claiming an
    approval was granted.
    """
    from agent import attest as agent_mod

    a = approvals_mod.create("run-1", "enable_s3_kms_encryption", "attest-demo-x", "why")
    approvals_mod.decide(a["approval_id"], approved=False)
    monkeypatch.setattr(agent_mod, "approvals", approvals_mod)

    captured = {}

    class FakeAgent:
        def __call__(self, message):
            captured["message"] = message
            return "ok"

    agent_mod.resume_after_decision("run-1", a["approval_id"], agent=FakeAgent())
    # A rejection must resume down the rejected path, not the apply path.
    assert "REJECTED" in captured["message"]
    assert "Do not attempt this change" in captured["message"]


# -- verdict integrity --------------------------------------------------------


def test_a_verdict_must_cite_evidence(monkeypatch):
    """An uncited verdict is an assertion, not a finding."""
    from tools import control_flow

    monkeypatch.setattr(control_flow, "current_run", lambda: "run-x")
    out = control_flow.record_finding.__wrapped__(
        control_id="ctrl-x", verdict="PASS", rationale="looks fine", evidence_ids=[]
    )
    assert "error" in out
    assert "evidence" in out["error"]


def test_an_invented_verdict_is_rejected(monkeypatch):
    from tools import control_flow

    monkeypatch.setattr(control_flow, "current_run", lambda: "run-x")
    out = control_flow.record_finding.__wrapped__(
        control_id="ctrl-x", verdict="PROBABLY_FINE", rationale="r", evidence_ids=["ev-1"]
    )
    assert "error" in out
    assert set(out["allowed"]) == {"PASS", "FAIL", "PARTIAL", "INDETERMINATE"}


def test_tools_never_write_findings_without_an_active_run(monkeypatch):
    """Guards against a stray tool call landing in the wrong run's records."""
    from tools import control_flow

    monkeypatch.setattr(control_flow, "current_run", lambda: "")
    for out in (
        control_flow.record_finding.__wrapped__("c", "PASS", "r", ["ev-1"]),
        control_flow.save_evidence.__wrapped__("t", {}),
        control_flow.request_approval.__wrapped__("a", "attest-demo-x", "r"),
    ):
        assert out.get("error") == "no active run"
