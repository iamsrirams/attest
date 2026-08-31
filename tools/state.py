"""Run state: DynamoDB records + evidence archived to S3.

Layout (PLAN §3):

  attest_runs       run_id                 -> one sweep
  attest_controls   run_id + control_id    -> one verdict
  attest_evidence   run_id + evidence_id   -> pointer to the raw JSON in S3
  attest_audit_log  run_id + seq           -> append-only tool-call log

Nested payloads are stored as JSON strings rather than native DynamoDB maps.
That avoids the float/Decimal conversion problem entirely and keeps a byte-exact
copy of what the tool returned, which is what "cite the evidence" requires.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError

from tools.config import TABLES, client, evidence_bucket, resource

VERDICTS = ("PASS", "FAIL", "PARTIAL", "INDETERMINATE")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{stamp}-{uuid.uuid4().hex[:6]}"


def _table(logical: str):
    return resource("dynamodb").Table(TABLES[logical])


# -- runs --------------------------------------------------------------------


def start_run(run_id: str, trigger: str = "manual", region: str = "") -> dict:
    item = {
        "run_id": run_id,
        "status": "RUNNING",
        "trigger": trigger,
        "region": region,
        "started_at": utcnow(),
        "finished_at": "",
        "summary": "",
    }
    _table("runs").put_item(Item=item)
    return item


def finish_run(run_id: str, status: str, summary: str = "") -> None:
    _table("runs").update_item(
        Key={"run_id": run_id},
        UpdateExpression="SET #s = :s, finished_at = :f, summary = :m",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": status,
            ":f": utcnow(),
            ":m": summary[:8000],
        },
    )


def get_run(run_id: str) -> dict | None:
    return _table("runs").get_item(Key={"run_id": run_id}).get("Item")


def list_runs(limit: int = 20) -> list[dict]:
    """Most recent runs first. Run ids are time-ordered, so a scan+sort is fine
    at this scale; a GSI would be premature."""
    items = _table("runs").scan(Limit=200).get("Items", [])
    return sorted(items, key=lambda r: r.get("started_at", ""), reverse=True)[:limit]


def previous_run(before_run_id: str) -> dict | None:
    """The most recent COMPLETED run before this one — the drift baseline."""
    runs = [
        r
        for r in list_runs(limit=50)
        if r["run_id"] != before_run_id and r.get("status") == "COMPLETE"
    ]
    return runs[0] if runs else None


# -- controls ----------------------------------------------------------------


def record_control(
    run_id: str,
    control_id: str,
    verdict: str,
    rationale: str,
    evidence_ids: list[str] | None = None,
    remediation: str = "",
) -> dict:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    item = {
        "run_id": run_id,
        "control_id": control_id,
        "verdict": verdict,
        "rationale": rationale[:2000],
        "evidence_ids": evidence_ids or [],
        "remediation": remediation[:1000],
        "recorded_at": utcnow(),
    }
    _table("controls").put_item(Item=item)
    return item


def get_controls(run_id: str) -> list[dict]:
    from boto3.dynamodb.conditions import Key

    return _table("controls").query(KeyConditionExpression=Key("run_id").eq(run_id)).get(
        "Items", []
    )


def controls_by_id(run_id: str) -> dict[str, dict]:
    return {c["control_id"]: c for c in get_controls(run_id)}


# -- evidence ----------------------------------------------------------------


def save_evidence_record(
    run_id: str,
    tool_name: str,
    payload: Any,
    control_id: str = "",
    args: dict | None = None,
) -> dict:
    """Archive a tool result to S3 and index it in DynamoDB.

    The returned `evidence_id` is what a verdict cites. The S3 object is the
    byte-exact tool output (already pseudonymized at the tool boundary).
    """
    evidence_id = f"ev-{uuid.uuid4().hex[:10]}"
    key = f"evidence/{run_id}/{evidence_id}.json"
    body = json.dumps(
        {
            "evidence_id": evidence_id,
            "run_id": run_id,
            "control_id": control_id,
            "tool": tool_name,
            "args": args or {},
            "collected_at": utcnow(),
            "result": payload,
        },
        indent=2,
        default=str,
    )

    s3_uri = ""
    try:
        client("s3").put_object(
            Bucket=evidence_bucket(),
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
        s3_uri = f"s3://{evidence_bucket()}/{key}"
    except ClientError as e:
        # Evidence archival failing must not abort a sweep; the DynamoDB index
        # still records that the tool ran and what it returned.
        s3_uri = f"UNARCHIVED:{e.response['Error']['Code']}"

    item = {
        "run_id": run_id,
        "evidence_id": evidence_id,
        "control_id": control_id,
        "tool": tool_name,
        "s3_uri": s3_uri,
        "collected_at": utcnow(),
        "result_json": body[:380000],  # DynamoDB item limit is 400KB
    }
    _table("evidence").put_item(Item=item)
    return {"evidence_id": evidence_id, "s3_uri": s3_uri, "tool": tool_name}


def get_evidence(run_id: str) -> list[dict]:
    from boto3.dynamodb.conditions import Key

    return _table("evidence").query(KeyConditionExpression=Key("run_id").eq(run_id)).get(
        "Items", []
    )


# -- audit log ---------------------------------------------------------------

_seq: dict[str, int] = {}


def append_audit(
    run_id: str, tool: str, args: dict, ok: bool, detail: str = ""
) -> None:
    """Append-only record of every tool invocation."""
    _seq[run_id] = _seq.get(run_id, 0) + 1
    _table("audit_log").put_item(
        Item={
            "run_id": run_id,
            "seq": f"{_seq[run_id]:06d}",
            "tool": tool,
            "args": json.dumps(args, default=str)[:4000],
            "ok": ok,
            "detail": detail[:2000],
            "at": utcnow(),
        }
    )


def get_audit(run_id: str) -> list[dict]:
    from boto3.dynamodb.conditions import Key

    items = (
        _table("audit_log")
        .query(KeyConditionExpression=Key("run_id").eq(run_id))
        .get("Items", [])
    )
    return sorted(items, key=lambda a: a.get("seq", ""))
