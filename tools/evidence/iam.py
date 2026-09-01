"""Read-only IAM evidence tools.

Docstrings are the agent's tool-selection surface: they name the control each
tool serves. Every tool returns structured data and reports errors AS DATA
(never raises) so a permissions gap becomes INDETERMINATE rather than aborting
the sweep (PLAN §7.5).
"""

from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timezone

from botocore.exceptions import ClientError
from strands import tool

from tools.config import MAX_KEY_AGE_DAYS, client
from tools.audit import audited
from tools.evidence._wrap import redacted

# Credential reports can list every user in the account. Keep tool output
# compact for the model's context window (PLAN §7) by returning only the rows
# that matter plus aggregate counts.
MAX_ROWS_RETURNED = 25


def _err(e: ClientError) -> dict:
    return {
        "error": e.response["Error"]["Code"],
        "message": e.response["Error"].get("Message", "")[:200],
    }


def _age_days(stamp: str) -> float | None:
    """IAM credential report timestamps -> age in days. 'N/A'/'not_supported' -> None."""
    if not stamp or stamp in ("N/A", "not_supported", "no_information"):
        return None
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0


def _fetch_credential_report(iam) -> list[dict]:
    """Generate (if needed) and download the IAM credential report as dict rows."""
    for _ in range(10):
        state = iam.generate_credential_report()["State"]
        if state == "COMPLETE":
            break
        time.sleep(2)
    raw = iam.get_credential_report()["Content"].decode("utf-8")
    return list(csv.DictReader(io.StringIO(raw)))


@tool
@audited
@redacted
def get_iam_credential_report() -> dict:
    """Fetch the IAM credential report: per-user MFA status and access key ages.

    This is the authoritative source for two controls at once:
      - ctrl-mfa-users (CC6.1): which users have console passwords without MFA
      - ctrl-key-rotation (CC6.1): which active access keys exceed the age threshold

    Returns the configured age threshold, aggregate counts, and only the
    offending users (capped), so the output stays compact.
    """
    iam = client("iam")
    try:
        rows = _fetch_credential_report(iam)
    except ClientError as e:
        return _err(e)

    users_total = 0
    console_users = 0
    no_mfa: list[dict] = []
    stale_keys: list[dict] = []
    active_keys = 0

    for r in rows:
        user = r["user"]
        if user == "<root_account>":
            # Root is evaluated by ctrl-mfa-root via get_account_summary.
            continue
        users_total += 1

        has_console = r.get("password_enabled") == "true"
        has_mfa = r.get("mfa_active") == "true"
        if has_console:
            console_users += 1
            if not has_mfa:
                no_mfa.append({"user": user, "password_enabled": True, "mfa_active": False})

        for n in ("1", "2"):
            if r.get(f"access_key_{n}_active") != "true":
                continue
            active_keys += 1
            age = _age_days(r.get(f"access_key_{n}_last_rotated", ""))
            if age is not None and age > MAX_KEY_AGE_DAYS:
                stale_keys.append(
                    {
                        "user": user,
                        "key_slot": int(n),
                        "age_days": round(age, 2),
                        "threshold_days": MAX_KEY_AGE_DAYS,
                    }
                )

    stale_keys.sort(key=lambda k: k["age_days"], reverse=True)

    return {
        "threshold_days": MAX_KEY_AGE_DAYS,
        "users_total": users_total,
        "console_users": console_users,
        "active_access_keys": active_keys,
        "users_without_mfa": no_mfa[:MAX_ROWS_RETURNED],
        "users_without_mfa_count": len(no_mfa),
        "keys_over_threshold": stale_keys[:MAX_ROWS_RETURNED],
        "keys_over_threshold_count": len(stale_keys),
        "truncated": len(no_mfa) > MAX_ROWS_RETURNED
        or len(stale_keys) > MAX_ROWS_RETURNED,
    }


@tool
@audited
@redacted
def list_iam_users_mfa() -> dict:
    """List every IAM user with their attached MFA devices and console access.

    Serves ctrl-mfa-users (CC6.1). This is the live-API alternative to the
    credential report: slower, but authoritative right now rather than as of the
    report's generation time. Useful for confirming a finding or when the
    credential report is unavailable.
    """
    iam = client("iam")
    out: list[dict] = []
    try:
        for page in iam.get_paginator("list_users").paginate():
            for u in page["Users"]:
                name = u["UserName"]
                devices = iam.list_mfa_devices(UserName=name)["MFADevices"]
                try:
                    iam.get_login_profile(UserName=name)
                    console = True
                except ClientError as e:
                    if e.response["Error"]["Code"] != "NoSuchEntity":
                        raise
                    console = False
                out.append(
                    {
                        "user": name,
                        "console_access": console,
                        "mfa_devices": len(devices),
                        "compliant": (not console) or len(devices) > 0,
                    }
                )
    except ClientError as e:
        return _err(e)

    offenders = [u for u in out if not u["compliant"]]
    return {
        "users": out[:MAX_ROWS_RETURNED],
        "count": len(out),
        "non_compliant": offenders,
        "non_compliant_count": len(offenders),
        "truncated": len(out) > MAX_ROWS_RETURNED,
    }


@tool
@audited
@redacted
def get_account_summary() -> dict:
    """Get the IAM account summary: root MFA status and root access key count.

    Serves ctrl-mfa-root (CC6.1). The root user cannot be constrained by IAM
    policy, so root MFA and the absence of root access keys are the two highest
    severity checks in the catalog.
    """
    iam = client("iam")
    try:
        s = iam.get_account_summary()["SummaryMap"]
    except ClientError as e:
        return _err(e)

    root_mfa = bool(s.get("AccountMFAEnabled", 0))
    root_keys = int(s.get("AccountAccessKeysPresent", 0))
    return {
        "root_mfa_enabled": root_mfa,
        "root_access_keys_present": root_keys,
        "compliant": root_mfa and root_keys == 0,
        "users": s.get("Users"),
        "mfa_devices": s.get("MFADevices"),
        "mfa_devices_in_use": s.get("MFADevicesInUse"),
    }
