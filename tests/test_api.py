"""API tests.

Deliberately narrow: these cover routing, validation and the run lock. The
model-dependent paths (POST /runs actually sweeping, /chat) are covered by the
integration run, not here.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    from api.app import app

    return TestClient(app)


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
