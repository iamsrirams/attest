"""Shared configuration and boto3 client construction.

Every module reads settings from here so that local CLI, container, Lambda and
AgentCore all behave identically. Nothing here contains account identifiers —
they are resolved at runtime from STS (PLAN §5.7).
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

import boto3
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(REPO_ROOT / ".env")

AWS_REGION = os.environ.get("AWS_REGION") or "us-east-1"

# Sonnet 4.5 requires the `us.` cross-region inference profile; the bare
# anthropic.* id is rejected for on-demand throughput. See BUILD_LOG.
BEDROCK_MODEL_ID = (
    os.environ.get("BEDROCK_MODEL_ID")
    or "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
)

TABLE_PREFIX = os.environ.get("TABLE_PREFIX") or "attest"

# Remediation tools refuse to write to anything not carrying this prefix
# (PLAN §10). This is a hard safety boundary, not a convention.
DEMO_PREFIX = os.environ.get("DEMO_PREFIX") or "attest-demo-"

MAX_KEY_AGE_DAYS = int(os.environ.get("MAX_KEY_AGE_DAYS") or "90")

SES_FROM = os.environ.get("SES_FROM") or ""
SES_TO = os.environ.get("SES_TO") or ""

APPROVAL_TTL_HOURS = 24

CATALOG_PATH = REPO_ROOT / "controls" / "catalog.yaml"


def table_name(logical: str) -> str:
    """runs -> attest_runs. Keeps every table under one configurable prefix."""
    return f"{TABLE_PREFIX}_{logical}"


TABLES = {
    "runs": table_name("runs"),
    "controls": table_name("controls"),
    "evidence": table_name("evidence"),
    "approvals": table_name("approvals"),
    "audit_log": table_name("audit_log"),
}


@functools.lru_cache(maxsize=None)
def account_id() -> str:
    """Resolved at runtime, never committed."""
    return client("sts").get_caller_identity()["Account"]


def evidence_bucket() -> str:
    """S3 bucket for evidence and trust packets.

    Defaults to an account-suffixed name so it is globally unique without the
    account id ever appearing in the repo.
    """
    configured = os.environ.get("S3_BUCKET")
    if configured:
        return configured
    return f"{TABLE_PREFIX}-evidence-{account_id()}"


@functools.lru_cache(maxsize=None)
def client(service: str):
    """Cached boto3 client pinned to the configured region."""
    return boto3.client(service, region_name=AWS_REGION)


@functools.lru_cache(maxsize=None)
def resource(service: str):
    """Cached boto3 resource pinned to the configured region."""
    return boto3.resource(service, region_name=AWS_REGION)
