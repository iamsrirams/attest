"""Read-only detective-control evidence tools: GuardDuty and AWS Config."""

from __future__ import annotations

from botocore.exceptions import ClientError
from strands import tool

from tools.config import AWS_REGION, client
from tools.evidence._wrap import redacted


def _err(e: ClientError) -> dict:
    return {
        "error": e.response["Error"]["Code"],
        "message": e.response["Error"].get("Message", "")[:200],
    }


@tool
@redacted
def get_guardduty_status() -> dict:
    """Check whether GuardDuty has an enabled detector in this region.

    Serves ctrl-guardduty (CC7.1). Reports every detector and its status. An
    empty detector list is a genuine FAIL (GuardDuty was never turned on), not
    an error.
    """
    gd = client("guardduty")
    try:
        ids = gd.list_detectors().get("DetectorIds", [])
    except ClientError as e:
        return _err(e)

    detectors: list[dict] = []
    for det_id in ids:
        try:
            d = gd.get_detector(DetectorId=det_id)
            detectors.append(
                {
                    "detector_id": det_id,
                    "status": d.get("Status"),
                    "finding_publishing_frequency": d.get("FindingPublishingFrequency"),
                }
            )
        except ClientError as e:
            detectors.append({"detector_id": det_id, "status": None, "error": e.response["Error"]["Code"]})

    enabled = [d for d in detectors if d.get("status") == "ENABLED"]
    return {
        "region": AWS_REGION,
        "detectors": detectors,
        "detector_count": len(detectors),
        "enabled_count": len(enabled),
        "compliant": len(enabled) > 0,
    }


@tool
@redacted
def get_config_recorder_status() -> dict:
    """Check whether an AWS Config recorder exists and is actively recording.

    Serves ctrl-config (CC7.2/CC7.3). Config supplies the configuration timeline
    auditors use to test that a control held continuously, not just on the day of
    the review. No recorder is a genuine FAIL, not an error.
    """
    cfg = client("config")
    try:
        recorders = cfg.describe_configuration_recorders().get(
            "ConfigurationRecorders", []
        )
        statuses = {
            s["name"]: s
            for s in cfg.describe_configuration_recorder_status().get(
                "ConfigurationRecordersStatus", []
            )
        }
    except ClientError as e:
        return _err(e)

    out: list[dict] = []
    for r in recorders:
        name = r.get("name")
        st = statuses.get(name, {})
        out.append(
            {
                "name": name,
                "recording": bool(st.get("recording")),
                "last_status": st.get("lastStatus"),
                "records_all_resources": bool(
                    r.get("recordingGroup", {}).get("allSupported")
                ),
                "includes_global_resources": bool(
                    r.get("recordingGroup", {}).get("includeGlobalResourceTypes")
                ),
            }
        )

    recording = [r for r in out if r["recording"]]
    return {
        "region": AWS_REGION,
        "recorders": out,
        "recorder_count": len(out),
        "recording_count": len(recording),
        "compliant": len(recording) > 0,
    }
