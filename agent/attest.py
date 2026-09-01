"""Agent construction, sweep entry point, and approve-and-resume.

Runtime-agnostic (PLAN §3): importable from the CLI, a container, Lambda or
AgentCore. Nothing here knows how it was invoked.

Note what this file does NOT do: it never calls an evidence tool. It builds the
agent, hands it the toolset, and gives it a run manifest. Which tools run, in
what order, and when to stop are the model's decisions (PLAN §5.1). If you ever
find yourself adding `list_s3_encryption_status()` to this module, that is the
moment this stops being an agent.
"""

from __future__ import annotations

import os

from strands import Agent
from strands.agent import SlidingWindowConversationManager
from strands.models import BedrockModel

from agent.instructions import (
    SYSTEM_PROMPT,
    approval_rejected,
    approval_resume,
    run_manifest,
)
from tools import approvals, control_flow, state, telemetry
from tools.config import AWS_REGION, BEDROCK_MODEL_ID
from tools.control_flow import (
    generate_trust_packet,
    get_approval_status,
    get_control_catalog,
    get_previous_run_findings,
    notify_user,
    record_finding,
    request_approval,
    save_evidence,
)
from tools.evidence import ALL_EVIDENCE_TOOLS
from tools.remediation.s3 import enable_s3_kms_encryption

# Ordered deterministically: prompt caching is a prefix match and the tool list
# is rendered before the system prompt, so a varying order would silently
# invalidate the cache on every run.
CONTROL_FLOW_TOOLS = [
    get_control_catalog,
    get_previous_run_findings,
    save_evidence,
    record_finding,
    request_approval,
    get_approval_status,
    notify_user,
    generate_trust_packet,
]

REMEDIATION_TOOLS = [enable_s3_kms_encryption]

ALL_TOOLS = [*ALL_EVIDENCE_TOOLS, *CONTROL_FLOW_TOOLS, *REMEDIATION_TOOLS]

# A full sweep is 40+ tool calls with sizeable JSON results. Cap the window so a
# long run cannot walk off the end of the context (PLAN §7).
WINDOW_SIZE = int(os.environ.get("ATTEST_WINDOW_SIZE", "40"))


def build_agent(callback_handler=None, trace_attrs: dict | None = None) -> Agent:
    """Construct the Strands agent. No AWS calls happen here."""
    telemetry.setup()
    return Agent(
        model=BedrockModel(model_id=BEDROCK_MODEL_ID, region_name=AWS_REGION),
        tools=ALL_TOOLS,
        system_prompt=SYSTEM_PROMPT,
        conversation_manager=SlidingWindowConversationManager(window_size=WINDOW_SIZE),
        callback_handler=callback_handler,
        name="attest",
        trace_attributes=trace_attrs or {},
    )


def run_sweep(trigger: str = "manual", run_id: str | None = None, agent: Agent | None = None):
    """Run one full sweep. Returns (run_id, agent, result)."""
    run_id = run_id or state.new_run_id()
    state.start_run(run_id, trigger=trigger, region=AWS_REGION)
    control_flow.set_current_run(run_id)

    agent = agent or build_agent(
        trace_attrs=telemetry.trace_attributes(run_id, trigger, AWS_REGION)
    )
    try:
        result = agent(run_manifest(run_id, AWS_REGION, trigger))
    except Exception as e:  # noqa: BLE001 — a crashed sweep must still be recorded
        state.finish_run(run_id, "FAILED", f"{type(e).__name__}: {e}")
        raise

    summary = str(result)
    state.finish_run(run_id, "COMPLETE", summary)
    return run_id, agent, result


def resume_after_decision(run_id: str, approval_id: str, agent: Agent | None = None):
    """Re-invoke the agent once a human has decided on an approval.

    The decision is read from DynamoDB rather than passed in, so the resume path
    cannot be told "it was approved" by anything but the record itself.
    """
    record = approvals.get(approval_id)
    if not record:
        raise ValueError(f"approval {approval_id} not found")

    control_flow.set_current_run(run_id)
    agent = agent or build_agent(
        trace_attrs=telemetry.trace_attributes(run_id, "approval-resume", AWS_REGION)
    )

    status = record.get("status")
    fields = (
        approval_id,
        record.get("action", ""),
        record.get("resource", ""),
        record.get("control_id", ""),
    )
    if status == approvals.APPROVED:
        message = approval_resume(*fields)
    elif status == approvals.REJECTED:
        message = approval_rejected(*fields)
    else:
        raise ValueError(
            f"approval {approval_id} is {status}; nothing to resume until a human decides"
        )

    return agent(message)
