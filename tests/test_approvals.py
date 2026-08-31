"""Tests for the approval gate — the security boundary of the product.

The threat model is a model that has been convinced, by a prompt injection or by
its own confusion, that a write is authorized. Every test here asks: can a write
happen without a matching human decision? The answer must be no in every case.

Uses moto so the gate logic is exercised against real DynamoDB semantics without
touching the live account.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import boto3
import pytest

moto = pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402

TABLE = "attest_approvals"


@pytest.fixture
def ddb(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    with mock_aws():
        boto3.client("dynamodb", region_name="us-east-1").create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "approval_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "approval_id", "AttributeType": "S"}
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        # Rebuild the cached boto3 clients inside the mock context.
        from tools import config

        config.client.cache_clear()
        config.resource.cache_clear()
        config.account_id.cache_clear()

        from tools import approvals

        importlib.reload(approvals)
        yield approvals

        config.client.cache_clear()
        config.resource.cache_clear()


def _make(approvals, action="enable_s3_kms_encryption", res="attest-demo-logs"):
    return approvals.create(
        run_id="run-test", action=action, resource_name=res, reason="because"
    )


# -- the gate must refuse everything that is not an explicit, matching approval


def test_pending_approval_is_refused(ddb):
    a = _make(ddb)
    ok, why = ddb.check(a["approval_id"], "enable_s3_kms_encryption", "attest-demo-logs")
    assert ok is False
    assert "PENDING" in why


def test_approved_approval_is_allowed(ddb):
    a = _make(ddb)
    ddb.decide(a["approval_id"], approved=True)
    ok, _ = ddb.check(a["approval_id"], "enable_s3_kms_encryption", "attest-demo-logs")
    assert ok is True


def test_rejected_approval_is_refused(ddb):
    a = _make(ddb)
    ddb.decide(a["approval_id"], approved=False)
    ok, why = ddb.check(a["approval_id"], "enable_s3_kms_encryption", "attest-demo-logs")
    assert ok is False
    assert "REJECTED" in why


def test_missing_approval_id_is_refused(ddb):
    ok, why = ddb.check("", "enable_s3_kms_encryption", "attest-demo-logs")
    assert ok is False
    assert "no approval_id" in why


def test_nonexistent_approval_is_refused(ddb):
    ok, why = ddb.check("apr-doesnotexist", "enable_s3_kms_encryption", "attest-demo-logs")
    assert ok is False
    assert "does not exist" in why


# -- binding: an approval authorizes exactly one action on exactly one resource


def test_approval_cannot_be_replayed_against_a_different_resource(ddb):
    """The core replay attack: approve bucket A, try to encrypt bucket B."""
    a = _make(ddb, res="attest-demo-logs")
    ddb.decide(a["approval_id"], approved=True)
    ok, why = ddb.check(
        a["approval_id"], "enable_s3_kms_encryption", "attest-demo-other"
    )
    assert ok is False
    assert "resource" in why


def test_approval_cannot_be_reused_for_a_different_action(ddb):
    a = _make(ddb, action="enable_s3_kms_encryption")
    ddb.decide(a["approval_id"], approved=True)
    ok, why = ddb.check(a["approval_id"], "disable_iam_access_key", "attest-demo-logs")
    assert ok is False
    assert "authorizes" in why


def test_approval_is_single_use(ddb):
    """Burning the approval prevents a second write from one decision."""
    a = _make(ddb)
    ddb.decide(a["approval_id"], approved=True)
    assert ddb.check(a["approval_id"], "enable_s3_kms_encryption", "attest-demo-logs")[0]

    ddb.mark_applied(a["approval_id"])
    ok, why = ddb.check(a["approval_id"], "enable_s3_kms_encryption", "attest-demo-logs")
    assert ok is False
    assert "already used" in why


def test_expired_approval_is_refused(ddb):
    """TTL deletion is eventually consistent, so expiry is checked in code too."""
    a = _make(ddb)
    ddb.decide(a["approval_id"], approved=True)
    past = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    ddb._table().update_item(
        Key={"approval_id": a["approval_id"]},
        UpdateExpression="SET expires_at = :e",
        ExpressionAttributeValues={":e": past},
    )
    ok, why = ddb.check(a["approval_id"], "enable_s3_kms_encryption", "attest-demo-logs")
    assert ok is False
    assert "expired" in why


def test_decision_cannot_be_flipped_after_the_fact(ddb):
    """A rejected approval must not become approved on a second call."""
    a = _make(ddb)
    ddb.decide(a["approval_id"], approved=False)
    ddb.decide(a["approval_id"], approved=True)
    assert ddb.get(a["approval_id"])["status"] == ddb.REJECTED


# -- the prefix guard is independent of approval


@pytest.mark.parametrize(
    "resource,allowed",
    [
        ("attest-demo-logs", True),
        ("attest-demo-anything", True),
        ("production-data", False),
        ("company-backups", False),
        ("", False),
        # Near-misses that must not slip through a naive substring check.
        ("not-attest-demo-logs", False),
        ("ATTEST-DEMO-logs", False),
    ],
)
def test_prefix_guard(ddb, resource, allowed):
    ok, _ = ddb.guard_resource(resource)
    assert ok is allowed


def test_prefix_guard_refuses_even_an_approved_production_resource(ddb):
    """Approval and the prefix guard are independent; both must pass."""
    a = _make(ddb, res="production-data")
    ddb.decide(a["approval_id"], approved=True)

    assert ddb.check(a["approval_id"], "enable_s3_kms_encryption", "production-data")[0] is True
    assert ddb.guard_resource("production-data")[0] is False
