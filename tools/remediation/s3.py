"""Approval-gated S3 remediation.

Every write in this module passes two independent checks before touching AWS:

  1. `approvals.check()`  — an APPROVED record bound to this exact
                            (action, resource), not yet used, not expired
  2. `approvals.guard_resource()` — the resource carries the demo prefix

Both are enforced here, in code. Neither is delegated to the system prompt, and
an approved request for a non-demo resource is still refused.

After writing, the tool re-reads the resource and returns the observed state, so
a control flips on evidence rather than on the assumption that the write worked.
"""

from __future__ import annotations

from botocore.exceptions import ClientError
from strands import tool

from tools import approvals
from tools.config import client
from tools.evidence.s3 import KMS_ALGORITHMS, _is_customer_managed

ACTION = "enable_s3_kms_encryption"


def _read_encryption(bucket: str) -> dict:
    """Observe the bucket's current default encryption."""
    s3 = client("s3")
    try:
        rule = s3.get_bucket_encryption(Bucket=bucket)[
            "ServerSideEncryptionConfiguration"
        ]["Rules"][0]["ApplyServerSideEncryptionByDefault"]
    except ClientError as e:
        return {"observed": None, "error": e.response["Error"]["Code"]}
    algorithm = rule.get("SSEAlgorithm")
    key_id = rule.get("KMSMasterKeyID")
    return {
        "observed": True,
        "algorithm": algorithm,
        "kms_key": key_id,
        "kms_customer_managed": _is_customer_managed(key_id),
        "meets_kms_requirement": algorithm in KMS_ALGORITHMS
        and _is_customer_managed(key_id),
    }


@tool
def enable_s3_kms_encryption(bucket: str, approval_id: str, kms_key: str) -> dict:
    """Re-key an S3 bucket's default encryption to SSE-KMS with a customer-managed key.

    Remediates ctrl-s3-encryption (CC6.7/CC6.8).

    REQUIRES an APPROVED approval bound to this exact action and bucket. Call
    `request_approval` first and wait for a human decision — this tool returns
    AWAITING_APPROVAL and writes nothing if the approval is missing, pending,
    rejected, expired, already used, or bound to a different resource.

    Args:
        bucket: the bucket to re-key. Must carry the demo prefix.
        approval_id: the id returned by `request_approval` for this exact change.
        kms_key: the customer-managed KMS key id, ARN or alias (e.g. alias/attest-demo).

    Returns the observed post-change encryption state, re-read from S3, so the
    verdict rests on evidence rather than on the write having been attempted.
    """
    # 1. Safety boundary first: refuse out-of-scope resources even if approved.
    ok, reason = approvals.guard_resource(bucket)
    if not ok:
        return {"status": "REFUSED", "bucket": bucket, "message": reason}

    # 2. The approval gate.
    ok, reason = approvals.check(approval_id, action=ACTION, resource=bucket)
    if not ok:
        return {
            "status": "AWAITING_APPROVAL",
            "bucket": bucket,
            "approval_id": approval_id,
            "message": reason,
        }

    before = _read_encryption(bucket)

    s3 = client("s3")
    try:
        s3.put_bucket_encryption(
            Bucket=bucket,
            ServerSideEncryptionConfiguration={
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "aws:kms",
                            "KMSMasterKeyID": kms_key,
                        },
                        "BucketKeyEnabled": True,
                    }
                ]
            },
        )
    except ClientError as e:
        return {
            "status": "FAILED",
            "bucket": bucket,
            "error": e.response["Error"]["Code"],
            "message": e.response["Error"].get("Message", "")[:300],
            "before": before,
        }

    # 3. Verify our own work by re-reading, not by trusting the write.
    after = _read_encryption(bucket)
    verified = bool(after.get("meets_kms_requirement"))

    if verified:
        # Burn the approval so it cannot authorize a second write.
        approvals.mark_applied(approval_id)

    return {
        "status": "APPLIED" if verified else "APPLIED_BUT_UNVERIFIED",
        "bucket": bucket,
        "approval_id": approval_id,
        "before": before,
        "after": after,
        "verified": verified,
        "message": (
            "Re-read the bucket after writing; it now uses SSE-KMS with a "
            "customer-managed key."
            if verified
            else "Write succeeded but the re-read did not confirm the requirement. "
            "Treat this control as INDETERMINATE and investigate."
        ),
    }
