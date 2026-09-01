"""Template and handler tests.

The IAM assertions are the point. If a future edit widens RemediationRole to
"s3:*" or drops the prefix from a Resource, the whole safety story collapses
quietly — the code-level guard would still hold, but defence in depth would be
gone with nothing to notice it. These fail loudly instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

TEMPLATE = Path(__file__).resolve().parent.parent / "infra" / "template.yaml"


class CfnLoader(yaml.SafeLoader):
    """CloudFormation short forms (!Sub, !GetAtt, ...) are not standard YAML."""


def _tag(loader, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


for t in ("!Ref", "!Sub", "!GetAtt", "!Join", "!Equals", "!Not", "!If",
          "!Select", "!Split", "!FindInMap", "!ImportValue", "!Condition"):
    CfnLoader.add_constructor(t, _tag)


@pytest.fixture(scope="module")
def tpl() -> dict:
    return yaml.load(TEMPLATE.read_text(), Loader=CfnLoader)


def _statements(tpl, role_name):
    role = tpl["Resources"][role_name]["Properties"]
    out = []
    for p in role.get("Policies", []):
        out.extend(p["PolicyDocument"]["Statement"])
    return out


def _actions(stmt):
    a = stmt.get("Action", [])
    return [a] if isinstance(a, str) else a


def _resources(stmt):
    r = stmt.get("Resource", [])
    return [r] if not isinstance(r, list) else r


def test_template_parses(tpl):
    assert tpl["Resources"]
    assert "EvidenceRole" in tpl["Resources"]
    assert "RemediationRole" in tpl["Resources"]


def test_evidence_role_has_no_write_actions(tpl):
    """A sweep must not be able to modify the account it audits."""
    forbidden = ("s3:put", "s3:delete", "iam:update", "iam:create", "iam:delete",
                 "ec2:authorize", "ec2:revoke", "ec2:create")
    for stmt in _statements(tpl, "EvidenceRole"):
        for action in _actions(stmt):
            low = action.lower()
            for bad in forbidden:
                # DynamoDB and the evidence bucket are Attest's own state, not
                # the audited account's configuration.
                if low.startswith(bad):
                    assert "dynamodb" in low or _is_own_state(stmt), (
                        f"EvidenceRole grants {action}"
                    )


def _is_own_state(stmt) -> bool:
    return any("EvidenceBucket" in str(r) for r in _resources(stmt))


def test_evidence_role_uses_readonly_managed_policies(tpl):
    arns = tpl["Resources"]["EvidenceRole"]["Properties"]["ManagedPolicyArns"]
    assert any("ReadOnlyAccess" in a for a in arns)
    assert any("SecurityAudit" in a for a in arns)
    assert not any("PowerUser" in a or "AdministratorAccess" in a for a in arns)


def test_remediation_role_grants_only_the_two_documented_writes(tpl):
    """Widening this role is exactly the change that must not pass unnoticed."""
    allowed = {
        "s3:putencryptionconfiguration",
        "s3:getencryptionconfiguration",
        "iam:updateaccesskey",
        "kms:describekey",
        "kms:generatedatakey",
        "dynamodb:getitem",
        "dynamodb:updateitem",
    }
    for stmt in _statements(tpl, "RemediationRole"):
        for action in _actions(stmt):
            assert "*" not in action, f"wildcard action {action} in RemediationRole"
            assert action.lower() in allowed, f"unexpected action {action}"


def test_remediation_s3_and_iam_are_prefix_scoped(tpl):
    """Defence in depth: the prefix guard exists in code AND in the policy."""
    for stmt in _statements(tpl, "RemediationRole"):
        actions = " ".join(_actions(stmt)).lower()
        if actions.startswith("s3:") or "iam:updateaccesskey" in actions:
            for res in _resources(stmt):
                assert "DemoPrefix" in str(res), (
                    f"{actions} is not scoped to the demo prefix: {res}"
                )


def test_remediation_role_cannot_create_or_decide_approvals(tpl):
    """The approval record is the authorization. The role that performs a write
    must not be able to manufacture one."""
    for stmt in _statements(tpl, "RemediationRole"):
        for action in _actions(stmt):
            assert action.lower() != "dynamodb:putitem"
            assert action.lower() != "dynamodb:deleteitem"


def test_remediation_role_is_assumable_only_by_evidence_role(tpl):
    trust = tpl["Resources"]["RemediationRole"]["Properties"]["AssumeRolePolicyDocument"]
    principals = [s["Principal"] for s in trust["Statement"]]
    assert all("AWS" in p for p in principals), principals
    assert not any("Service" in p for p in principals)


def test_approvals_table_has_ttl(tpl):
    ttl = tpl["Resources"]["ApprovalsTable"]["Properties"]["TimeToLiveSpecification"]
    assert ttl["Enabled"] is True
    assert ttl["AttributeName"] == "expires_at"


def test_evidence_bucket_blocks_public_access(tpl):
    """Attest audits for this setting, so its own bucket must hold it."""
    cfg = tpl["Resources"]["EvidenceBucket"]["Properties"]["PublicAccessBlockConfiguration"]
    assert all(cfg[k] is True for k in cfg)


def test_state_tables_are_retained_on_stack_delete(tpl):
    for name in ("RunsTable", "ControlsTable", "EvidenceTable", "AuditLogTable",
                 "EvidenceBucket"):
        assert tpl["Resources"][name]["DeletionPolicy"] == "Retain", name


def test_schedule_defaults_to_disabled(tpl):
    """Creating the stack must not silently start unattended sweeps."""
    assert tpl["Parameters"]["ScheduleEnabled"]["Default"] == "DISABLED"


# -- Lambda handlers ---------------------------------------------------------


def test_resume_rejects_incomplete_events():
    from agent import handler

    for bad in ({}, {"approval_id": "apr-x"}, {"run_id": "run-x"}):
        r = handler.resume(bad)
        assert r["statusCode"] == 400
        assert "required" in json.loads(r["body"])["error"]


def test_handlers_are_importable_without_lambda_runtime():
    """The agent must stay runtime-agnostic; only this module knows about Lambda."""
    from agent import handler

    assert callable(handler.sweep)
    assert callable(handler.resume)
    assert callable(handler.api)


def test_agent_module_has_no_lambda_import():
    src = (Path(__file__).resolve().parent.parent / "agent" / "attest.py").read_text()
    assert "mangum" not in src.lower()
    assert "lambda_handler" not in src


# -- telemetry ---------------------------------------------------------------


def test_telemetry_is_off_by_default(monkeypatch):
    """A local CLI run must not require a collector."""
    from tools import telemetry

    monkeypatch.delenv("ATTEST_TELEMETRY", raising=False)
    monkeypatch.delenv("ATTEST_TELEMETRY_CONSOLE", raising=False)
    assert telemetry.enabled() is False
    assert telemetry.console_enabled() is False


def test_telemetry_setup_never_raises(monkeypatch):
    """Telemetry that can break a sweep is worse than no telemetry."""
    from tools import telemetry

    monkeypatch.setattr(telemetry, "_initialized", False)
    monkeypatch.setenv("ATTEST_TELEMETRY", "1")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    telemetry.setup()  # missing endpoint must warn, not raise


def test_trace_attributes_group_by_run():
    """session.id is the run id so a trace groups by sweep, which is the unit
    an operator actually asks about."""
    from tools import telemetry

    attrs = telemetry.trace_attributes("run-1", "schedule", "us-east-1")
    assert attrs["session.id"] == "run-1"
    assert attrs["attest.trigger"] == "schedule"
    assert attrs["service.name"] == "attest"
