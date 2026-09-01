"""Read-only EC2 network and volume evidence tools."""

from __future__ import annotations

from botocore.exceptions import ClientError
from strands import tool

from tools.config import AWS_REGION, client
from tools.audit import audited
from tools.evidence._wrap import redacted

RISKY_PORTS = {22: "SSH", 3389: "RDP"}
OPEN_CIDRS = {"0.0.0.0/0", "::/0"}


def _err(e: ClientError) -> dict:
    return {
        "error": e.response["Error"]["Code"],
        "message": e.response["Error"].get("Message", "")[:200],
    }


def _rule_is_open(perm: dict) -> list[str]:
    """Return the open CIDRs in this permission, if any."""
    cidrs = [r["CidrIp"] for r in perm.get("IpRanges", []) if r.get("CidrIp") in OPEN_CIDRS]
    cidrs += [
        r["CidrIpv6"]
        for r in perm.get("Ipv6Ranges", [])
        if r.get("CidrIpv6") in OPEN_CIDRS
    ]
    return cidrs


def _covered_ports(perm: dict) -> list[int]:
    """Which risky ports this permission exposes, accounting for ranges and -1."""
    proto = perm.get("IpProtocol")
    if proto == "-1":
        return sorted(RISKY_PORTS)
    if proto not in ("tcp", "6"):
        return []
    lo = perm.get("FromPort")
    hi = perm.get("ToPort")
    if lo is None or hi is None:
        return []
    return [p for p in RISKY_PORTS if lo <= p <= hi]


@tool
@audited
@redacted
def list_open_security_groups() -> dict:
    """Find security groups allowing 0.0.0.0/0 or ::/0 ingress on port 22 or 3389.

    Serves ctrl-open-sgs (CC6.6). Handles port ranges and the "all protocols"
    (-1) rule, so a group opening 0-65535 is caught as exposing both SSH and RDP.
    Returns each offending group with the specific port, protocol and CIDR, so
    the finding can be cited precisely.
    """
    ec2 = client("ec2")
    try:
        groups = []
        for page in ec2.get_paginator("describe_security_groups").paginate():
            groups.extend(page["SecurityGroups"])
    except ClientError as e:
        return _err(e)

    offenders: list[dict] = []
    for g in groups:
        violations: list[dict] = []
        for perm in g.get("IpPermissions", []):
            cidrs = _rule_is_open(perm)
            if not cidrs:
                continue
            for port in _covered_ports(perm):
                violations.append(
                    {
                        "port": port,
                        "service": RISKY_PORTS[port],
                        "protocol": perm.get("IpProtocol"),
                        "cidrs": cidrs,
                    }
                )
        if violations:
            offenders.append(
                {
                    "group_id": g["GroupId"],
                    "group_name": g.get("GroupName"),
                    "vpc_id": g.get("VpcId"),
                    "violations": violations,
                }
            )

    return {
        "region": AWS_REGION,
        "security_groups_scanned": len(groups),
        "open_groups": offenders,
        "open_group_count": len(offenders),
        "compliant": len(offenders) == 0,
    }


@tool
@audited
@redacted
def get_default_ebs_encryption() -> dict:
    """Check whether EBS encryption-by-default is enabled for this account/region.

    Serves ctrl-ebs-default (CC6.7). This is an account-level, per-region
    setting: when on, every future volume is encrypted regardless of what the
    person launching it asks for.
    """
    ec2 = client("ec2")
    try:
        enabled = ec2.get_ebs_encryption_by_default()["EbsEncryptionByDefault"]
    except ClientError as e:
        return _err(e)

    result = {"region": AWS_REGION, "ebs_encryption_by_default": bool(enabled), "compliant": bool(enabled)}
    try:
        result["default_kms_key_id"] = ec2.get_ebs_default_kms_key_id()["KmsKeyId"]
    except ClientError as e:
        result["default_kms_key_error"] = e.response["Error"]["Code"]
    return result
