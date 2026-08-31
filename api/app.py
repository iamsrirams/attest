"""FastAPI backend.

Polling only — no websockets, no SSE (PLAN §3). A sweep is long-running, so it
is started as a background task and the client polls the run's status and
timeline. That keeps the dashboard trivial and the deployment story portable
across local, container, Lambda and AgentCore.
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from tools import approvals, state
from tools.config import AWS_REGION, BEDROCK_MODEL_ID
from tools.redact import redact

app = FastAPI(
    title="Attest",
    description="Autonomous compliance agent for AWS.",
    version="0.1.0",
)

# The dashboard is served separately in dev (Vite on :5173).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# One sweep at a time. A second trigger is a no-op rather than a parallel run
# that would interleave findings into the same tables (PLAN §12, run lock).
_sweep_lock = threading.Lock()
_active: dict[str, str] = {"run_id": ""}


class DecisionBody(BaseModel):
    decided_by: str = "dashboard"


class SweepBody(BaseModel):
    trigger: str = "api"


@app.get("/health")
def health() -> dict:
    return {"ok": True, "region": AWS_REGION, "model": BEDROCK_MODEL_ID}


# -- runs --------------------------------------------------------------------


def _run_sweep_bg(trigger: str) -> None:
    from agent.attest import run_sweep

    try:
        run_sweep(trigger=trigger)
    finally:
        _active["run_id"] = ""
        if _sweep_lock.locked():
            _sweep_lock.release()


@app.post("/runs")
def trigger_sweep(body: SweepBody, background: BackgroundTasks) -> dict:
    """Start a sweep. Returns immediately; poll GET /runs/{run_id}."""
    if not _sweep_lock.acquire(blocking=False):
        return {
            "started": False,
            "reason": "a sweep is already running",
            "run_id": _active["run_id"],
        }
    run_id = state.new_run_id()
    _active["run_id"] = run_id
    # The agent allocates its own run id, so seed the record here and let the
    # background task adopt it, keeping the response immediate.
    state.start_run(run_id, trigger=body.trigger, region=AWS_REGION)

    def task() -> None:
        from agent.attest import run_sweep

        try:
            run_sweep(trigger=body.trigger, run_id=run_id)
        except Exception as e:  # noqa: BLE001
            state.finish_run(run_id, "FAILED", f"{type(e).__name__}: {e}")
        finally:
            _active["run_id"] = ""
            if _sweep_lock.locked():
                _sweep_lock.release()

    background.add_task(task)
    return {"started": True, "run_id": run_id}


@app.get("/runs")
def list_runs(limit: int = 20) -> dict:
    return {"runs": state.list_runs(limit=limit)}


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = state.get_run(run_id)
    if not run:
        raise HTTPException(404, f"run {run_id} not found")
    controls = state.get_controls(run_id)
    counts: dict[str, int] = {}
    for c in controls:
        counts[c["verdict"]] = counts.get(c["verdict"], 0) + 1
    return {"run": run, "counts": counts, "control_count": len(controls)}


@app.get("/runs/{run_id}/controls")
def get_controls(run_id: str) -> dict:
    return {"controls": state.get_controls(run_id)}


@app.get("/runs/{run_id}/timeline")
def get_timeline(run_id: str) -> dict:
    """The agent's tool calls in order — what the dashboard renders live."""
    return {"timeline": state.get_audit(run_id)}


@app.get("/runs/{run_id}/evidence")
def get_evidence(run_id: str) -> dict:
    return {"evidence": state.get_evidence(run_id)}


@app.get("/runs/{run_id}/packet", response_class=HTMLResponse)
def get_packet(run_id: str) -> HTMLResponse:
    from packet.render import build_packet, render_html

    try:
        return HTMLResponse(render_html(build_packet(run_id)))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/runs/{run_id}/packet.json")
def get_packet_json(run_id: str) -> Any:
    from packet.render import build_packet

    try:
        return build_packet(run_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


# -- approvals ---------------------------------------------------------------


@app.get("/approvals")
def list_approvals(run_id: str = "") -> dict:
    # Approval records store the real resource name because remediation needs
    # it, but the dashboard is on screen during demos and recordings — so what
    # the browser receives goes through the same redaction as everything else.
    return {"approvals": redact(approvals.list_pending(run_id=run_id))}


@app.get("/approvals/{approval_id}")
def get_approval(approval_id: str) -> dict:
    record = approvals.get(approval_id)
    if not record:
        raise HTTPException(404, f"approval {approval_id} not found")
    return redact(record)


def _decide_and_resume(approval_id: str, approved: bool, decided_by: str) -> dict:
    record = approvals.decide(approval_id, approved=approved, decided_by=decided_by)
    if not record:
        raise HTTPException(404, f"approval {approval_id} not found")

    # Resuming re-invokes the model, which can take a while; run it in a thread
    # so the dashboard's click returns immediately and the timeline shows the
    # work arriving.
    def task() -> None:
        from agent.attest import resume_after_decision

        try:
            resume_after_decision(record["run_id"], approval_id)
        except Exception as e:  # noqa: BLE001
            state.append_audit(
                record["run_id"], "resume_after_decision", {"approval_id": approval_id},
                False, f"{type(e).__name__}: {e}",
            )

    threading.Thread(target=task, daemon=True).start()
    return {"approval_id": approval_id, "status": record["status"], "resuming": True}


@app.post("/approvals/{approval_id}/approve")
def approve(approval_id: str, body: DecisionBody) -> dict:
    return _decide_and_resume(approval_id, True, body.decided_by)


@app.post("/approvals/{approval_id}/reject")
def reject(approval_id: str, body: DecisionBody) -> dict:
    return _decide_and_resume(approval_id, False, body.decided_by)


# -- ad-hoc chat -------------------------------------------------------------


class ChatBody(BaseModel):
    question: str
    run_id: str = ""


@app.post("/chat")
def chat(body: ChatBody) -> dict:
    """Ask the agent a question against the live account.

    Same tools, same read-only guarantees; the agent decides what to call. Used
    in the demo to show it is genuinely an agent rather than a fixed report.
    """
    from agent.attest import build_agent
    from tools import control_flow

    if body.run_id:
        control_flow.set_current_run(body.run_id)

    agent = build_agent()
    result = agent(body.question)
    return {"answer": str(result)}


@app.get("/catalog")
def catalog() -> dict:
    from tools.control_flow import get_control_catalog

    return get_control_catalog()
