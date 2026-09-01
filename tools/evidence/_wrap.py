"""Backwards-compatible re-export.

`redacted` now lives in `tools.redact`, next to the machinery it uses, because
it is needed by remediation tools too — not only evidence tools.
"""

from tools.redact import redacted

__all__ = ["redacted"]
