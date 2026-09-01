"""OpenTelemetry setup.

Strands emits a span per model call and per tool call on its own; this module
only chooses where they go and attaches the attributes that make them useful —
run id, region, trigger — so a trace can be tied back to a specific sweep.

Deliberately best-effort. Telemetry that can break a compliance sweep is worse
than no telemetry, so every failure here degrades to a warning.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_initialized = False


def enabled() -> bool:
    """Off unless asked for. Local CLI runs should not need a collector."""
    return os.environ.get("ATTEST_TELEMETRY", "0").lower() in ("1", "true", "yes")


def console_enabled() -> bool:
    return os.environ.get("ATTEST_TELEMETRY_CONSOLE", "0").lower() in ("1", "true", "yes")


def setup() -> bool:
    """Wire up exporters once per process. Returns whether tracing is active.

    OTLP endpoint comes from the standard OTEL_EXPORTER_OTLP_ENDPOINT variable.
    In AWS that is the ADOT collector sidecar, which forwards to CloudWatch;
    locally it is usually a container on :4318.
    """
    global _initialized
    if _initialized:
        return True
    if not (enabled() or console_enabled()):
        return False

    try:
        from strands.telemetry import StrandsTelemetry

        telemetry = StrandsTelemetry()
        if console_enabled():
            telemetry.setup_console_exporter()
        if enabled():
            endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
            if not endpoint:
                log.warning(
                    "ATTEST_TELEMETRY is on but OTEL_EXPORTER_OTLP_ENDPOINT is "
                    "unset; spans will not be exported"
                )
            else:
                telemetry.setup_otlp_exporter()
                telemetry.setup_meter(enable_otlp_exporter=True)
        _initialized = True
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("telemetry setup failed, continuing without it: %s", e)
        return False


def trace_attributes(run_id: str, trigger: str, region: str) -> dict:
    """Attributes attached to every span the agent emits during a sweep.

    `session.id` is the run id so a trace groups by sweep, which is the unit an
    operator actually asks about ("what happened in last night's run").
    """
    return {
        "session.id": run_id,
        "attest.run_id": run_id,
        "attest.trigger": trigger,
        "attest.region": region,
        "service.name": "attest",
    }
