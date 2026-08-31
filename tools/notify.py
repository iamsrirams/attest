"""SES notifications.

Degrades to a no-op with a clear reason rather than raising: a missing SES
identity must not abort a sweep.
"""

from __future__ import annotations

from botocore.exceptions import ClientError

from tools.config import SES_FROM, SES_TO, client


def send(subject: str, body: str) -> tuple[bool, str]:
    if not SES_FROM or not SES_TO:
        return False, "SES_FROM/SES_TO not configured; skipped"
    try:
        client("ses").send_email(
            Source=SES_FROM,
            Destination={"ToAddresses": [SES_TO]},
            Message={
                "Subject": {"Data": subject[:200]},
                "Body": {"Text": {"Data": body[:60000]}},
            },
        )
        return True, f"sent to {SES_TO}"
    except ClientError as e:
        return False, f"{e.response['Error']['Code']}: {e.response['Error'].get('Message','')[:200]}"


def notify_approval_request(record: dict) -> tuple[bool, str]:
    """Email the owner that a change is waiting on their decision."""
    subject = f"[Attest] Approval needed: {record['action']} on {record['resource']}"
    body = (
        f"Attest wants to make a change and needs your approval.\n\n"
        f"  Action:    {record['action']}\n"
        f"  Resource:  {record['resource']}\n"
        f"  Control:   {record.get('control_id') or '-'}\n"
        f"  Run:       {record['run_id']}\n\n"
        f"Why:\n  {record['reason']}\n\n"
        f"Approve or reject:\n"
        f"  attest approve {record['approval_id']}\n"
        f"  attest reject  {record['approval_id']}\n\n"
        f"This request expires at {record['expires_at_iso']} and is bound to that\n"
        f"exact action and resource — it cannot be reused for anything else.\n"
    )
    return send(subject, body)
