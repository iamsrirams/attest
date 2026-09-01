"""Model retry policy.

Strands retries throttling out of the box. That is not the only transient
failure worth surviving: a model can also emit a malformed tool-use sequence,
which the service rejects with `modelStreamErrorException`. Observed in a real
sweep — the run died at the first occurrence and the summary was lost, while
the verdicts recorded up to that point stayed orphaned in a FAILED run.

For a nightly unattended sweep that is the difference between "eight of ten
controls assessed, two unknown" and "nothing". These are retried because a
second attempt usually produces valid output; everything else still fails fast,
so a genuine bug is not buried under retries.
"""

from __future__ import annotations

import logging

from strands import ModelRetryStrategy

log = logging.getLogger(__name__)

# Substrings identifying transient model-side faults. Matched on the message
# because botocore raises these as generic ClientError/EventLoopException
# rather than distinct classes.
TRANSIENT = (
    "modelstreamerrorexception",
    "invalid sequence as part of tooluse",
    "serviceunavailable",
    "internalserverexception",
    "read timeout",
    "connection reset",
)


class SweepRetryStrategy(ModelRetryStrategy):
    """Throttling, plus transient model-stream faults."""

    def is_retryable(self, exception: Exception) -> bool:
        if super().is_retryable(exception):
            return True

        text = f"{type(exception).__name__} {exception}".lower()
        # EventLoopException wraps the underlying cause, so check that too.
        cause = getattr(exception, "__cause__", None)
        if cause:
            text += f" {type(cause).__name__} {cause}".lower()

        if any(marker in text for marker in TRANSIENT):
            log.warning("retrying transient model fault: %s", str(exception)[:160])
            return True
        return False
