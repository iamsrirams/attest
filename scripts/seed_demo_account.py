"""Seed the scratch account with REAL misconfigurations for Attest to find.

Everything created here is genuine AWS state — an actually-unencrypted bucket, an
actually-MFA-less user, an actually-open security group. Attest's verdicts come
from reading the account, never from this file (PLAN §5.4).

Idempotent. Every resource carries the `attest-demo-` prefix so the remediation
tools' safety boundary applies to all of it.

    ./.venv/bin/python scripts/seed_demo_account.py
    ./.venv/bin/python scripts/seed_demo_account.py --clean
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.config import AWS_REGION, account_id, client  # noqa: E402

BUCKET_UNENCRYPTED = "attest-demo-logs"
BUCKET_DENIED = "attest-demo-denied"
USER_NO_MFA = "attest-demo-contractor-alice"
USER_WITH_KEY = "attest-demo-legacy-service"
SG_NAME = "attest-demo-open"
KMS_ALIAS = "alias/attest-demo"

# Bucket names are global; suffix with the account id so two people running this
# do not collide. The account id is resolved at runtime, never committed.
def _bucket(base: str) -> str:
    return f"{base}-{account_id()}"


def _exists(fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
        return True
    except ClientError:
        return False


# --------------------------------------------------------------------------
# ctrl-s3-encryption: a bucket on SSE-S3 only, i.e. no customer-managed key
#
# S3 has applied SSE-S3 (AES256) as an unremovable baseline to every bucket
# since January 2023: `delete_bucket_encryption` returns success but the bucket
# reverts to AES256. A genuinely unencrypted bucket can no longer be created, so
# the control tests encryption *strength* instead — SSE-KMS with a
# customer-managed key — and this bucket fails it by using only SSE-S3.
# --------------------------------------------------------------------------
def seed_kms_key(findings: list[str]) -> str | None:
    """Create the customer-managed CMK that remediation will re-key the bucket to.

    A CMK costs ~$1/month. This is the only recurring charge the demo creates.
    """
    kms = client("kms")
    try:
        return kms.describe_key(KeyId=KMS_ALIAS)["KeyMetadata"]["Arn"]
    except ClientError:
        pass

    key = kms.create_key(
        Description="Attest demo: target key for S3 SSE-KMS remediation",
        KeyUsage="ENCRYPT_DECRYPT",
        Tags=[{"TagKey": "project", "TagValue": "attest-demo"}],
    )["KeyMetadata"]
    kms.create_alias(AliasName=KMS_ALIAS, TargetKeyId=key["KeyId"])
    print(f"  + created customer-managed KMS key {KMS_ALIAS} (~$1/month)")
    return key["Arn"]


def seed_sse_s3_only_bucket(findings: list[str]) -> None:
    s3 = client("s3")
    name = _bucket(BUCKET_UNENCRYPTED)

    if not _exists(s3.head_bucket, Bucket=name):
        kwargs = {"Bucket": name}
        if AWS_REGION != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": AWS_REGION}
        s3.create_bucket(**kwargs)
        print(f"  + created bucket {name}")
    else:
        print(f"  = bucket {name} exists")

    # Force the bucket back to SSE-S3 so re-running the seed after a remediation
    # demo restores the failing state.
    s3.put_bucket_encryption(
        Bucket=name,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
            ]
        },
    )
    print(f"  ! set {name} to SSE-S3 only (no customer-managed key)")

    # Keep public access blocked — we are demonstrating the encryption control,
    # and an actually-public bucket in a real account is not worth the risk.
    s3.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    findings.append(
        f"ctrl-s3-encryption  FAIL  {name} uses SSE-S3, not a customer-managed KMS key"
    )


# --------------------------------------------------------------------------
# AccessDenied -> INDETERMINATE, exercised live
# --------------------------------------------------------------------------
def seed_denied_bucket(findings: list[str]) -> None:
    s3 = client("s3")
    name = _bucket(BUCKET_DENIED)
    caller = client("sts").get_caller_identity()["Arn"]

    if not _exists(s3.head_bucket, Bucket=name):
        kwargs = {"Bucket": name}
        if AWS_REGION != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": AWS_REGION}
        s3.create_bucket(**kwargs)
        print(f"  + created bucket {name}")
    else:
        print(f"  = bucket {name} exists")

    s3.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )

    # Deny reading the encryption config so the agent meets a real AccessDenied
    # and must return INDETERMINATE rather than guessing.
    #
    # NOTE: a bucket policy cannot deny the account root without risking lockout,
    # so we deny the *specific* calling principal. Deleting the policy (--clean)
    # is always possible via s3:DeleteBucketPolicy, which we do not deny.
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AttestDemoDenyReadEncryption",
                "Effect": "Deny",
                "Principal": {"AWS": caller},
                "Action": "s3:GetEncryptionConfiguration",
                "Resource": f"arn:aws:s3:::{name}",
            }
        ],
    }
    try:
        s3.put_bucket_policy(Bucket=name, Policy=json.dumps(policy))
        print(f"  ! denied s3:GetEncryptionConfiguration on {name}")
        findings.append(
            f"ctrl-s3-encryption  INDETERMINATE  {name} denies GetEncryptionConfiguration"
        )
    except ClientError as e:
        print(f"    (put_bucket_policy failed: {e.response['Error']['Code']})")


# --------------------------------------------------------------------------
# ctrl-mfa-users: a console user with no MFA device
# --------------------------------------------------------------------------
def seed_user_without_mfa(findings: list[str]) -> None:
    iam = client("iam")

    if not _exists(iam.get_user, UserName=USER_NO_MFA):
        iam.create_user(
            UserName=USER_NO_MFA,
            Tags=[{"Key": "project", "Value": "attest-demo"}],
        )
        print(f"  + created IAM user {USER_NO_MFA}")
    else:
        print(f"  = IAM user {USER_NO_MFA} exists")

    # A console login profile with no MFA is exactly the finding we want. The
    # password is random, never printed, and the user has zero attached policies.
    if not _exists(iam.get_login_profile, UserName=USER_NO_MFA):
        import secrets

        iam.create_login_profile(
            UserName=USER_NO_MFA,
            Password=secrets.token_urlsafe(24) + "aA1!",
            PasswordResetRequired=False,
        )
        print(f"  ! gave {USER_NO_MFA} a console password and NO MFA device")
    findings.append(f"ctrl-mfa-users  FAIL  {USER_NO_MFA} has console access, no MFA")


# --------------------------------------------------------------------------
# ctrl-key-rotation: a real, active access key
# --------------------------------------------------------------------------
def seed_access_key(findings: list[str]) -> None:
    iam = client("iam")

    if not _exists(iam.get_user, UserName=USER_WITH_KEY):
        iam.create_user(
            UserName=USER_WITH_KEY,
            Tags=[{"Key": "project", "Value": "attest-demo"}],
        )
        print(f"  + created IAM user {USER_WITH_KEY}")
    else:
        print(f"  = IAM user {USER_WITH_KEY} exists")

    keys = iam.list_access_keys(UserName=USER_WITH_KEY)["AccessKeyMetadata"]
    if not keys:
        resp = iam.create_access_key(UserName=USER_WITH_KEY)
        # Deliberately not printed or stored — the key's existence and age are
        # what the control reads; the secret is irrelevant to Attest.
        del resp
        print(f"  ! created a real access key for {USER_WITH_KEY}")
    else:
        print(f"  = {USER_WITH_KEY} already has {len(keys)} access key(s)")

    findings.append(
        f"ctrl-key-rotation  FAIL (with MAX_KEY_AGE_DAYS=1)  "
        f"{USER_WITH_KEY} has an active key"
    )


# --------------------------------------------------------------------------
# ctrl-open-sgs: 0.0.0.0/0 on port 22
# --------------------------------------------------------------------------
def _default_vpc_id(ec2) -> str | None:
    vpcs = ec2.describe_vpcs(
        Filters=[{"Name": "isDefault", "Values": ["true"]}]
    )["Vpcs"]
    return vpcs[0]["VpcId"] if vpcs else None


def seed_open_security_group(findings: list[str]) -> None:
    ec2 = client("ec2")
    vpc_id = _default_vpc_id(ec2)
    if not vpc_id:
        print("  ~ no default VPC in this region; skipping open security group")
        return

    existing = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [SG_NAME]}]
    )["SecurityGroups"]
    if existing:
        sg_id = existing[0]["GroupId"]
        print(f"  = security group {SG_NAME} exists ({sg_id})")
    else:
        sg_id = ec2.create_security_group(
            GroupName=SG_NAME,
            Description="Attest demo: intentionally open SSH. Not attached to anything.",
            VpcId=vpc_id,
            TagSpecifications=[
                {
                    "ResourceType": "security-group",
                    "Tags": [{"Key": "project", "Value": "attest-demo"}],
                }
            ],
        )["GroupId"]
        print(f"  + created security group {SG_NAME} ({sg_id})")

    # No instance is ever attached to this group, so nothing is actually
    # reachable — but the misconfiguration the control reads is genuine.
    try:
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "IpRanges": [
                        {"CidrIp": "0.0.0.0/0", "Description": "attest demo finding"}
                    ],
                }
            ],
        )
        print(f"  ! opened port 22 to 0.0.0.0/0 on {sg_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise
        print("    (ingress rule already present)")

    findings.append(f"ctrl-open-sgs  FAIL  {SG_NAME} allows 0.0.0.0/0 on port 22")


# --------------------------------------------------------------------------
# teardown
# --------------------------------------------------------------------------
def _empty_and_delete_bucket(s3, name: str) -> None:
    if not _exists(s3.head_bucket, Bucket=name):
        print(f"  = bucket {name} absent")
        return
    try:
        s3.delete_bucket_policy(Bucket=name)
    except ClientError:
        pass
    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=name):
        objects = [
            {"Key": o["Key"], "VersionId": o["VersionId"]}
            for key in ("Versions", "DeleteMarkers")
            for o in page.get(key, [])
        ]
        if objects:
            s3.delete_objects(Bucket=name, Delete={"Objects": objects})
    s3.delete_bucket(Bucket=name)
    print(f"  - deleted bucket {name}")


def clean() -> None:
    s3, iam, ec2 = client("s3"), client("iam"), client("ec2")

    for base in (BUCKET_UNENCRYPTED, BUCKET_DENIED):
        _empty_and_delete_bucket(s3, _bucket(base))

    for user in (USER_NO_MFA, USER_WITH_KEY):
        if not _exists(iam.get_user, UserName=user):
            print(f"  = IAM user {user} absent")
            continue
        for key in iam.list_access_keys(UserName=user)["AccessKeyMetadata"]:
            iam.delete_access_key(UserName=user, AccessKeyId=key["AccessKeyId"])
            print(f"  - deleted access key for {user}")
        try:
            iam.delete_login_profile(UserName=user)
        except ClientError:
            pass
        for pol in iam.list_attached_user_policies(UserName=user)["AttachedPolicies"]:
            iam.detach_user_policy(UserName=user, PolicyArn=pol["PolicyArn"])
        iam.delete_user(UserName=user)
        print(f"  - deleted IAM user {user}")

    groups = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [SG_NAME]}]
    )["SecurityGroups"]
    for g in groups:
        ec2.delete_security_group(GroupId=g["GroupId"])
        print(f"  - deleted security group {g['GroupId']}")
    if not groups:
        print(f"  = security group {SG_NAME} absent")

    # KMS keys cannot be deleted immediately; schedule the shortest window (7d)
    # so the ~$1/month charge stops.
    kms = client("kms")
    try:
        key_id = kms.describe_key(KeyId=KMS_ALIAS)["KeyMetadata"]["KeyId"]
        kms.delete_alias(AliasName=KMS_ALIAS)
        kms.schedule_key_deletion(KeyId=key_id, PendingWindowInDays=7)
        print(f"  - scheduled KMS key {KMS_ALIAS} for deletion in 7 days")
    except ClientError as e:
        print(f"  = KMS key {KMS_ALIAS}: {e.response['Error']['Code']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clean", action="store_true", help="tear the demo state down")
    args = ap.parse_args()

    print(f"region: {AWS_REGION}\n")

    if args.clean:
        clean()
        print("\n✓ demo state removed")
        return 0

    findings: list[str] = []
    print("KMS:")
    seed_kms_key(findings)
    print("\nS3:")
    seed_sse_s3_only_bucket(findings)
    seed_denied_bucket(findings)
    print("\nIAM:")
    seed_user_without_mfa(findings)
    seed_access_key(findings)
    print("\nEC2:")
    seed_open_security_group(findings)

    print("\n" + "=" * 72)
    print("The account is now genuinely failing these controls:")
    print("=" * 72)
    for f in findings:
        print(f"  {f}")
    print(
        "\nSet MAX_KEY_AGE_DAYS=1 for the demo so the freshly-created key trips "
        "ctrl-key-rotation.\nNothing above is simulated — Attest reads this state "
        "from the live account."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
