"""Create Attest's own infrastructure: DynamoDB tables + evidence bucket.

Idempotent. CloudFormation replaces this at Phase 6; this exists so Phase 0-5
can run without waiting on IaC.

    ./.venv/bin/python scripts/bootstrap_aws.py
    ./.venv/bin/python scripts/bootstrap_aws.py --clean
"""

from __future__ import annotations

import argparse
import sys

from botocore.exceptions import ClientError

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from tools.config import AWS_REGION, TABLES, client, evidence_bucket  # noqa: E402

# key schema per table: (hash_key, range_key or None)
TABLE_KEYS = {
    TABLES["runs"]: ("run_id", None),
    TABLES["controls"]: ("run_id", "control_id"),
    TABLES["evidence"]: ("run_id", "evidence_id"),
    TABLES["approvals"]: ("approval_id", None),
    TABLES["audit_log"]: ("run_id", "seq"),
}

# seq is a zero-padded string so lexical sort == numeric sort
ATTR_TYPES = {"seq": "S"}


def create_tables() -> None:
    ddb = client("dynamodb")
    existing = set()
    paginator = ddb.get_paginator("list_tables")
    for page in paginator.paginate():
        existing.update(page["TableNames"])

    for name, (hash_key, range_key) in TABLE_KEYS.items():
        if name in existing:
            print(f"  = table {name} already exists")
            continue

        key_schema = [{"AttributeName": hash_key, "KeyType": "HASH"}]
        attrs = [
            {"AttributeName": hash_key, "AttributeType": ATTR_TYPES.get(hash_key, "S")}
        ]
        if range_key:
            key_schema.append({"AttributeName": range_key, "KeyType": "RANGE"})
            attrs.append(
                {
                    "AttributeName": range_key,
                    "AttributeType": ATTR_TYPES.get(range_key, "S"),
                }
            )

        ddb.create_table(
            TableName=name,
            KeySchema=key_schema,
            AttributeDefinitions=attrs,
            BillingMode="PAY_PER_REQUEST",
            Tags=[{"Key": "project", "Value": "attest"}],
        )
        print(f"  + created table {name}")

    for name in TABLE_KEYS:
        ddb.get_waiter("table_exists").wait(TableName=name)

    # Approvals expire after 24h (PLAN §3). TTL attribute is `expires_at`,
    # epoch seconds. Enabling TTL is idempotent-ish: it errors if already on.
    try:
        ddb.update_time_to_live(
            TableName=TABLES["approvals"],
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"},
        )
        print(f"  + enabled TTL on {TABLES['approvals']} (expires_at)")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationException":
            print(f"  = TTL already enabled on {TABLES['approvals']}")
        else:
            raise


def create_bucket() -> None:
    s3 = client("s3")
    bucket = evidence_bucket()
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"  = bucket {bucket} already exists")
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchBucket", "403"):
            raise
        kwargs = {"Bucket": bucket}
        if AWS_REGION != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": AWS_REGION}
        s3.create_bucket(**kwargs)
        print(f"  + created bucket {bucket}")

    # Attest's own bucket must model the behaviour it audits for.
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
            ]
        },
    )
    s3.put_bucket_versioning(
        Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
    )
    print("  ✓ bucket hardened (block public access, AES256 default SSE, versioning)")


def clean() -> None:
    ddb = client("dynamodb")
    for name in TABLE_KEYS:
        try:
            ddb.delete_table(TableName=name)
            print(f"  - deleted table {name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                print(f"  = table {name} absent")
            else:
                raise
    print(
        f"\n  NOTE: evidence bucket {evidence_bucket()} was NOT deleted "
        "(it holds run history). Remove it by hand if you really mean to."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clean", action="store_true", help="delete Attest's tables")
    args = ap.parse_args()

    print(f"region: {AWS_REGION}\n")
    if args.clean:
        clean()
        return 0

    print("DynamoDB:")
    create_tables()
    print("\nS3:")
    create_bucket()
    print("\n✓ bootstrap complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
