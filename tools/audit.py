"""Tool-call audit logging.

Every tool the agent invokes is recorded, not only the control-flow ones. Two
reasons: an audit trail that omits the actual AWS reads is not an audit trail,
and the dashboard timeline is how a viewer sees the agent working — a row
saying `save_evidence -> ev-dcbed19e33` conveys nothing, while
`list_open_security_groups -> 9 groups open on 22/3389` conveys the work.

Failures are logged too, with the error code, so a sweep that hit a permissions
wall can be reconstructed afterwards.
"""

from __future__ import annotations

import functools
import time
from typing import Any, Callable

# Short, human-readable summaries per tool. Anything unlisted falls back to a
# generic shape description, so a new tool still logs something useful.
def _summarize(name: str, result: Any) -> str:
    if not isinstance(result, dict):
        return str(result)[:120]

    if "error" in result and len(result) <= 3:
        return f"error: {result['error']}"

    def n(*keys: str) -> int | None:
        for k in keys:
            if isinstance(result.get(k), int):
                return result[k]
        return None

    if name == "list_s3_encryption_status":
        bad, unread = n("not_meeting_kms_requirement_count"), n("unreadable_count")
        parts = [f"{result.get('count', '?')} buckets"]
        if bad:
            parts.append(f"{bad} without a customer-managed key")
        if unread:
            parts.append(f"{unread} unreadable")
        return ", ".join(parts)

    if name == "list_s3_public_access":
        return f"{result.get('count','?')} buckets, {n('not_fully_blocked_count') or 0} not fully blocked"

    if name == "get_iam_credential_report":
        return (
            f"{result.get('users_total','?')} users, "
            f"{result.get('users_without_mfa_count',0)} without MFA, "
            f"{result.get('keys_over_threshold_count',0)} stale keys"
        )

    if name == "list_iam_users_mfa":
        return f"{result.get('count','?')} users, {result.get('non_compliant_count',0)} non-compliant"

    if name == "get_account_summary":
        return f"root MFA {'on' if result.get('root_mfa_enabled') else 'OFF'}, {result.get('root_access_keys_present',0)} root keys"

    if name == "list_open_security_groups":
        return f"{result.get('security_groups_scanned','?')} groups scanned, {result.get('open_group_count',0)} open on 22/3389"

    if name == "get_cloudtrail_status":
        return f"{result.get('count',0)} trails, compliant={result.get('compliant')}"

    if name == "get_guardduty_status":
        return f"{result.get('detector_count',0)} detectors, {result.get('enabled_count',0)} enabled"

    if name == "get_config_recorder_status":
        return f"{result.get('recorder_count',0)} recorders, {result.get('recording_count',0)} recording"

    if name == "get_default_ebs_encryption":
        return f"encryption by default {'on' if result.get('ebs_encryption_by_default') else 'OFF'}"

    keys = ", ".join(list(result)[:4])
    return f"returned {{{keys}}}"


def audited(fn: Callable) -> Callable:
    """Log this tool's invocation, outcome and a summary to the audit log.

    Placed inside `@tool` and outside `@redacted`, so what is logged is the
    already-pseudonymized result — the audit log is rendered in the dashboard
    and must not become the one place real names survive.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Imported lazily: tools.state imports config, which some tools import
        # at module load, and a cycle here would break tool registration.
        from tools import control_flow, state

        started = time.time()
        try:
            result = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 — log, then let it propagate
            run_id = control_flow.current_run()
            if run_id:
                state.append_audit(
                    run_id, fn.__name__, kwargs, False, f"{type(e).__name__}: {e}"[:300]
                )
            raise

        run_id = control_flow.current_run()
        if run_id:
            ok = not (isinstance(result, dict) and "error" in result and len(result) <= 3)
            detail = _summarize(fn.__name__, result)
            ms = int((time.time() - started) * 1000)
            state.append_audit(run_id, fn.__name__, kwargs, ok, f"{detail} ({ms}ms)")
        return result

    return wrapper
