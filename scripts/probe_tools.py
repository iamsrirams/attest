"""Call every evidence tool directly against the live account and print results.

This is NOT the sweep — there is no agent here, and this file must never grow
into one (PLAN §5.1). It exists so the tools themselves can be verified against
real AWS independently of the model.

    ./.venv/bin/python scripts/probe_tools.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.evidence import ALL_EVIDENCE_TOOLS  # noqa: E402


def main() -> int:
    failures = 0
    for t in ALL_EVIDENCE_TOOLS:
        name = t.tool_spec["name"]
        print(f"\n{'=' * 72}\n{name}\n{'=' * 72}")
        try:
            result = t()
        except Exception as e:  # noqa: BLE001 - probe reports, never crashes
            failures += 1
            print(f"  RAISED {type(e).__name__}: {e}")
            continue
        text = json.dumps(result, indent=2, default=str)
        print(text if len(text) < 2200 else text[:2200] + "\n  ...(truncated)")
        if isinstance(result, dict) and "error" in result and len(result) <= 2:
            print(f"  ^ tool-level error (reported as data, not raised)")

    print(f"\n{'=' * 72}")
    print(f"{len(ALL_EVIDENCE_TOOLS)} tools probed, {failures} raised")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
