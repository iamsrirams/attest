"""Read-only S3 evidence tools."""

from __future__ import annotations

from botocore.exceptions import ClientError
from strands import tool

from tools.config import client
from tools.evidence._wrap import redacted

# Retained for completeness. Since January 2023 S3 applies SSE-S3 (AES256) as an
# unremovable baseline to every bucket, so in practice this code no longer
# appears: `delete_bucket_encryption` succeeds but the bucket reverts to AES256.
# The control therefore tests the *strength* of encryption, not its presence.
NO_ENCRYPTION_CODE = "ServerSideEncryptionConfigurationNotFoundError"

KMS_ALGORITHMS = {"aws:kms", "aws:kms:dsse"}


def _is_customer_managed(key_id: str | None) -> bool:
    """AWS-managed keys (`aws/s3`) do not satisfy a customer-managed-key requirement."""
    if not key_id:
        return False
    return "alias/aws/" not in key_id


@tool
@redacted
def list_s3_encryption_status() -> dict:
    """Check the default encryption algorithm and key type for every S3 bucket.

    Serves ctrl-s3-encryption (CC6.7/CC6.8).

    Note that S3 has applied SSE-S3 (AES256) as an unremovable baseline to every
    bucket since January 2023, so "is it encrypted at all" is no longer a
    meaningful question. This control therefore tests encryption *strength*:
    whether the bucket uses SSE-KMS with a customer-managed key, which is what
    enterprise reviews and key-rotation requirements ask for.

    Per bucket:
      algorithm             — "AES256" (SSE-S3) or "aws:kms" (SSE-KMS)
      kms_customer_managed  — true only for a customer-managed CMK, not aws/s3
      meets_kms_requirement — the control's pass condition for this bucket
      error                 — set when the bucket could not be read, in which
                              case it is INDETERMINATE and must NOT be a PASS
    """
    s3 = client("s3")
    out: list[dict] = []
    try:
        buckets = s3.list_buckets().get("Buckets", [])
    except ClientError as e:
        return {
            "error": e.response["Error"]["Code"],
            "message": e.response["Error"].get("Message", "")[:200],
        }

    for b in buckets:
        name = b["Name"]
        try:
            rule = s3.get_bucket_encryption(Bucket=name)[
                "ServerSideEncryptionConfiguration"
            ]["Rules"][0]["ApplyServerSideEncryptionByDefault"]
            algorithm = rule.get("SSEAlgorithm")
            key_id = rule.get("KMSMasterKeyID")
            cmk = _is_customer_managed(key_id)
            out.append(
                {
                    "bucket": name,
                    "encrypted": True,
                    "algorithm": algorithm,
                    "kms_key": key_id,
                    "kms_customer_managed": cmk,
                    "meets_kms_requirement": algorithm in KMS_ALGORITHMS and cmk,
                }
            )
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == NO_ENCRYPTION_CODE:
                out.append(
                    {
                        "bucket": name,
                        "encrypted": False,
                        "algorithm": None,
                        "kms_customer_managed": False,
                        "meets_kms_requirement": False,
                    }
                )
            else:
                out.append(
                    {
                        "bucket": name,
                        "encrypted": None,
                        "algorithm": None,
                        "meets_kms_requirement": None,
                        "error": code,
                    }
                )

    sse_s3_only = [b["bucket"] for b in out if b["meets_kms_requirement"] is False]
    unreadable = [
        {"bucket": b["bucket"], "error": b["error"]}
        for b in out
        if b["meets_kms_requirement"] is None
    ]
    return {
        "requirement": "SSE-KMS with a customer-managed key",
        "buckets": out,
        "count": len(out),
        "not_meeting_kms_requirement": sse_s3_only,
        "not_meeting_kms_requirement_count": len(sse_s3_only),
        "unreadable": unreadable,
        "unreadable_count": len(unreadable),
    }


@tool
@redacted
def list_s3_public_access() -> dict:
    """Check the four Block Public Access settings on every S3 bucket.

    Serves ctrl-s3-public (CC6.6). A bucket passes only when all four of
    BlockPublicAcls, IgnorePublicAcls, BlockPublicPolicy and RestrictPublicBuckets
    are true. A missing public access block configuration is a FAIL, not an error.
    Buckets that cannot be read report `error` and are INDETERMINATE.
    """
    s3 = client("s3")
    out: list[dict] = []
    try:
        buckets = s3.list_buckets().get("Buckets", [])
    except ClientError as e:
        return {
            "error": e.response["Error"]["Code"],
            "message": e.response["Error"].get("Message", "")[:200],
        }

    for b in buckets:
        name = b["Name"]
        try:
            cfg = s3.get_public_access_block(Bucket=name)[
                "PublicAccessBlockConfiguration"
            ]
            all_on = all(
                cfg.get(k, False)
                for k in (
                    "BlockPublicAcls",
                    "IgnorePublicAcls",
                    "BlockPublicPolicy",
                    "RestrictPublicBuckets",
                )
            )
            out.append({"bucket": name, "fully_blocked": all_on, "settings": cfg})
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "NoSuchPublicAccessBlockConfiguration":
                out.append(
                    {
                        "bucket": name,
                        "fully_blocked": False,
                        "settings": None,
                        "note": "no public access block configuration set",
                    }
                )
            else:
                out.append({"bucket": name, "fully_blocked": None, "error": code})

    exposed = [b["bucket"] for b in out if b["fully_blocked"] is False]
    unreadable = [
        {"bucket": b["bucket"], "error": b["error"]}
        for b in out
        if b["fully_blocked"] is None
    ]
    return {
        "buckets": out,
        "count": len(out),
        "not_fully_blocked": exposed,
        "not_fully_blocked_count": len(exposed),
        "unreadable": unreadable,
        "unreadable_count": len(unreadable),
    }
