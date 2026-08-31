"""Human-in-the-loop approval records.

This module is the security boundary of the whole product (PLAN §5.3). A model
asserting that something was approved is not authorization: a remediation tool
must call `check()` and get `(True, ...)` back before it touches AWS.

An approval is bound to an exact `(action, resource)` pair. Approving
"encrypt bucket A" cannot be replayed to encrypt bucket B, and it cannot be
reused for a different action on the same bucket.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

from tools.config import APPROVAL_TTL_HOURS, DEMO_PREFIX, TABLES, resource
from tools.state import utcnow

PENDING = "PENDING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
APPLIED = "APPLIED"


def _table():
    return resource("dynamodb").Table(TABLES["approvals"])


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def create(
    run_id: str,
    action: str,
    resource_name: str,
    reason: str,
    control_id: str = "",
    params: dict | None = None,
) -> dict:
    """Create a PENDING approval. Returns the record, including approval_id."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=APPROVAL_TTL_HOURS)
    item = {
        "approval_id": f"apr-{uuid.uuid4().hex[:10]}",
        "run_id": run_id,
        "control_id": control_id,
        "action": action,
        "resource": resource_name,
        "params": params or {},
        "reason": reason[:2000],
        "status": PENDING,
        "created_at": utcnow(),
        "decided_at": "",
        "decided_by": "",
        # DynamoDB TTL attribute — the record self-destructs after 24h so a
        # stale approval cannot be redeemed later.
        "expires_at": _epoch(expires),
        "expires_at_iso": expires.isoformat(timespec="seconds"),
    }
    _table().put_item(Item=item)
    return item


def get(approval_id: str) -> dict | None:
    return _table().get_item(Key={"approval_id": approval_id}).get("Item")


def decide(approval_id: str, approved: bool, decided_by: str = "human") -> dict | None:
    """Record a human decision. Only a PENDING approval can be decided."""
    record = get(approval_id)
    if not record:
        return None
    if record.get("status") != PENDING:
        return record  # already decided; never re-open

    status = APPROVED if approved else REJECTED
    _table().update_item(
        Key={"approval_id": approval_id},
        UpdateExpression="SET #s = :s, decided_at = :d, decided_by = :b",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":d": utcnow(), ":b": decided_by},
    )
    return get(approval_id)


def mark_applied(approval_id: str) -> None:
    """Burn the approval so it cannot authorize a second write."""
    _table().update_item(
        Key={"approval_id": approval_id},
        UpdateExpression="SET #s = :s, applied_at = :a",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": APPLIED, ":a": utcnow()},
    )


def list_pending(run_id: str = "") -> list[dict]:
    items = _table().scan(Limit=200).get("Items", [])
    items = [i for i in items if i.get("status") == PENDING]
    if run_id:
        items = [i for i in items if i.get("run_id") == run_id]
    return sorted(items, key=lambda i: i.get("created_at", ""), reverse=True)


def check(approval_id: str, action: str, resource: str) -> tuple[bool, str]:
    """The gate. Returns (allowed, reason).

    Every condition below is a real way this could be abused, so each is checked
    explicitly rather than collapsed into one truthy test.
    """
    if not approval_id:
        return False, "no approval_id supplied; call request_approval first"

    try:
        record = get(approval_id)
    except ClientError as e:
        return False, f"could not read approval record: {e.response['Error']['Code']}"

    if not record:
        return False, f"approval {approval_id} does not exist"

    status = record.get("status")
    if status == PENDING:
        return False, f"approval {approval_id} is still PENDING a human decision"
    if status == REJECTED:
        return False, f"approval {approval_id} was REJECTED by the human"
    if status == APPLIED:
        return False, f"approval {approval_id} was already used; request a new one"
    if status != APPROVED:
        return False, f"approval {approval_id} has unexpected status {status!r}"

    # Binding: the approval authorizes exactly one action on exactly one resource.
    if record.get("action") != action:
        return (
            False,
            f"approval {approval_id} authorizes {record.get('action')!r}, not {action!r}",
        )
    if record.get("resource") != resource:
        return (
            False,
            f"approval {approval_id} authorizes resource "
            f"{record.get('resource')!r}, not {resource!r}",
        )

    # Belt and braces: TTL removal is eventually consistent, so verify the
    # expiry ourselves rather than trusting the record's absence.
    expires = record.get("expires_at")
    if expires and int(expires) < _epoch(datetime.now(timezone.utc)):
        return False, f"approval {approval_id} expired at {record.get('expires_at_iso')}"

    return True, "approved"


def guard_resource(resource_name: str) -> tuple[bool, str]:
    """Hard safety boundary (PLAN §10): never write outside the demo prefix.

    Independent of the approval check — an approved request for a production
    bucket is still refused. Both must pass.
    """
    if not resource_name:
        return False, "no resource supplied"
    if not resource_name.startswith(DEMO_PREFIX):
        return (
            False,
            f"refusing to modify {resource_name!r}: remediation is restricted to "
            f"resources prefixed {DEMO_PREFIX!r}",
        )
    return True, "within demo prefix"
