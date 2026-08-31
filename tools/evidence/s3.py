"""Read-only S3 evidence tools."""

from __future__ import annotations

from botocore.exceptions import ClientError
from strands import tool

from tools.config import client

# S3 returns this code when a bucket simply has no default SSE configured.
# It is a legitimate FAIL, not an error. Any OTHER code (AccessDenied, etc.)
# means we could not observe the bucket and the verdict must be INDETERMINATE.
NO_ENCRYPTION_CODE = "ServerSideEncryptionConfigurationNotFoundError"


@tool
def list_s3_encryption_status() -> dict:
    """Check default server-side encryption for every S3 bucket in the account.

    Serves ctrl-s3-encryption (CC6.7/CC6.8).

    Each bucket reports `encrypted` as one of:
      true  — a default SSE rule exists (algorithm included)
      false — no default SSE rule (a real FAIL)
      null  — the bucket could not be read; `error` holds the AWS error code,
              and this bucket must be treated as INDETERMINATE, not as a pass.
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
            out.append(
                {
                    "bucket": name,
                    "encrypted": True,
                    "algorithm": rule.get("SSEAlgorithm"),
                    "kms_key": rule.get("KMSMasterKeyID"),
                }
            )
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == NO_ENCRYPTION_CODE:
                out.append({"bucket": name, "encrypted": False, "algorithm": None})
            else:
                out.append(
                    {"bucket": name, "encrypted": None, "algorithm": None, "error": code}
                )

    unencrypted = [b["bucket"] for b in out if b["encrypted"] is False]
    unreadable = [
        {"bucket": b["bucket"], "error": b["error"]}
        for b in out
        if b["encrypted"] is None
    ]
    return {
        "buckets": out,
        "count": len(out),
        "unencrypted": unencrypted,
        "unencrypted_count": len(unencrypted),
        "unreadable": unreadable,
        "unreadable_count": len(unreadable),
    }


@tool
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
