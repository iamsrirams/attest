"""Read-only CloudTrail evidence tools."""

from __future__ import annotations

from botocore.exceptions import ClientError
from strands import tool

from tools.config import client
from tools.evidence._wrap import redacted


@tool
@redacted
def get_cloudtrail_status() -> dict:
    """Check whether a multi-region CloudTrail trail exists and is actively logging.

    Serves ctrl-cloudtrail (CC7.2). For each trail this reports whether it is
    multi-region, whether logging is currently on, and whether log file
    validation is enabled. The control passes only when at least one trail is
    BOTH multi-region AND logging.
    """
    ct = client("cloudtrail")
    try:
        trails = ct.describe_trails(includeShadowTrails=False).get("trailList", [])
    except ClientError as e:
        return {
            "error": e.response["Error"]["Code"],
            "message": e.response["Error"].get("Message", "")[:200],
        }

    out: list[dict] = []
    for t in trails:
        entry = {
            "name": t.get("Name"),
            "multi_region": bool(t.get("IsMultiRegionTrail")),
            "log_file_validation": bool(t.get("LogFileValidationEnabled")),
            "home_region": t.get("HomeRegion"),
        }
        try:
            status = ct.get_trail_status(Name=t["TrailARN"])
            entry["is_logging"] = bool(status.get("IsLogging"))
            entry["latest_delivery_error"] = status.get("LatestDeliveryError")
        except ClientError as e:
            entry["is_logging"] = None
            entry["error"] = e.response["Error"]["Code"]
        out.append(entry)

    compliant = [t for t in out if t["multi_region"] and t.get("is_logging") is True]
    unreadable = [t for t in out if t.get("is_logging") is None]
    return {
        "trails": out,
        "count": len(out),
        "multi_region_logging_trails": [t["name"] for t in compliant],
        "compliant": len(compliant) > 0,
        "unreadable_count": len(unreadable),
    }
