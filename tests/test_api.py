"""API tests.

Deliberately narrow: these cover routing, validation and the run lock. The
model-dependent paths (POST /runs actually sweeping, /chat) are covered by the
integration run, not here.

Endpoints that read DynamoDB run against moto rather than the live account.
Without that these are integration tests wearing a unit test's clothes — they
pass on a laptop with credentials and fail in CI, which is exactly what
happened.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("moto")

import boto3  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from moto import mock_aws  # noqa: E402

TABLE_KEYS = {
    "attest_runs": ("run_id", None),
    "attest_controls": ("run_id", "control_id"),
    "attest_evidence": ("run_id", "evidence_id"),
    "attest_approvals": ("approval_id", None),
    "attest_audit_log": ("run_id", "seq"),
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        for name, (hash_key, range_key) in TABLE_KEYS.items():
            schema = [{"AttributeName": hash_key, "KeyType": "HASH"}]
            attrs = [{"AttributeName": hash_key, "AttributeType": "S"}]
            if range_key:
                schema.append({"AttributeName": range_key, "KeyType": "RANGE"})
                attrs.append({"AttributeName": range_key, "AttributeType": "S"})
            ddb.create_table(
                TableName=name,
                KeySchema=schema,
                AttributeDefinitions=attrs,
                BillingMode="PAY_PER_REQUEST",
            )

        from tools import config

        config.client.cache_clear()
        config.resource.cache_clear()
        config.account_id.cache_clear()

        from api.app import app

        yield TestClient(app)

        config.client.cache_clear()
        config.resource.cache_clear()
        config.account_id.cache_clear()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["region"]
    # The model id must be the us.-prefixed inference profile; the bare
    # anthropic.* id is rejected by Bedrock for on-demand throughput.
    assert body["model"].startswith("us.anthropic.")


def test_unknown_run_is_404(client):
    assert client.get("/runs/run-does-not-exist").status_code == 404


def test_unknown_approval_is_404(client):
    assert client.get("/approvals/apr-nope").status_code == 404


def test_unknown_packet_is_404(client):
    assert client.get("/runs/run-nope/packet").status_code == 404
    assert client.get("/runs/run-nope/packet.json").status_code == 404


def test_catalog_is_served(client):
    body = client.get("/catalog").json()
    assert body["control_count"] == 10
    ids = {c["id"] for c in body["controls"]}
    assert "ctrl-s3-encryption" in ids


def test_catalog_never_prescribes_an_order(client):
    """candidate_tools are hints. If a control ever gains an ordered `steps` or
    `sequence` key, the sweep has become a script (PLAN §5.1)."""
    for c in client.get("/catalog").json()["controls"]:
        assert "steps" not in c
        assert "sequence" not in c
        assert isinstance(c.get("candidate_tools", []), list)


def test_second_sweep_is_refused_while_one_is_running(client, monkeypatch):
    """The run lock stops a duplicate trigger interleaving findings."""
    from api import app as app_module

    acquired = app_module._sweep_lock.acquire(blocking=False)
    assert acquired
    app_module._active["run_id"] = "run-in-flight"
    try:
        body = client.post("/runs", json={"trigger": "test"}).json()
        assert body["started"] is False
        assert body["run_id"] == "run-in-flight"
    finally:
        app_module._active["run_id"] = ""
        app_module._sweep_lock.release()
