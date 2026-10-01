"""Keep the API token out of uvicorn's access log: `?token=` (EventSource) is rewritten to `token=***`."""
from __future__ import annotations

import logging
import re

_TOKEN_RE = re.compile(r"([?&](?:access_)?token=)[^&\s]*", re.IGNORECASE)        # ?token= and RFC 6750 ?access_token=


def redact(text: str) -> str:
    return _TOKEN_RE.sub(r"\1***", text)


class RedactTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def install_access_log_redaction() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactTokenFilter) for f in logger.filters):
        logger.addFilter(RedactTokenFilter())
