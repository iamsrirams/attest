"""The redaction boundary for evidence tools.

Applied *inside* `@tool`, so the redacted result is what the agent receives:

    @tool
    @redacted
    def list_s3_encryption_status() -> dict:
        ...

`functools.wraps` preserves the signature and docstring, which Strands reads to
build the tool spec the model selects on.
"""

from __future__ import annotations

import functools
from typing import Any, Callable

from tools.redact import redact, redaction_enabled


def redacted(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Pseudonymize a tool's return value before the agent ever sees it."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = fn(*args, **kwargs)
        if not redaction_enabled():
            return result
        return redact(result)

    return wrapper
