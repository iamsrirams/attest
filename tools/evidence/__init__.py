"""Read-only evidence tools.

`ALL_EVIDENCE_TOOLS` is the set handed to the agent. It is a REGISTRY, not an
execution order — the agent decides what to call and in what sequence
(PLAN §5.1). Nothing may iterate this list to "run the sweep".
"""

from tools.evidence.detect import get_config_recorder_status, get_guardduty_status
from tools.evidence.iam import (
    get_account_summary,
    get_iam_credential_report,
    list_iam_users_mfa,
)
from tools.evidence.network import get_default_ebs_encryption, list_open_security_groups
from tools.evidence.s3 import list_s3_encryption_status, list_s3_public_access
from tools.evidence.trail import get_cloudtrail_status

ALL_EVIDENCE_TOOLS = [
    get_iam_credential_report,
    list_iam_users_mfa,
    get_account_summary,
    list_s3_encryption_status,
    list_s3_public_access,
    get_cloudtrail_status,
    get_guardduty_status,
    list_open_security_groups,
    get_default_ebs_encryption,
    get_config_recorder_status,
]

__all__ = [
    "ALL_EVIDENCE_TOOLS",
    "get_account_summary",
    "get_cloudtrail_status",
    "get_config_recorder_status",
    "get_default_ebs_encryption",
    "get_guardduty_status",
    "get_iam_credential_report",
    "list_iam_users_mfa",
    "list_open_security_groups",
    "list_s3_encryption_status",
    "list_s3_public_access",
]
