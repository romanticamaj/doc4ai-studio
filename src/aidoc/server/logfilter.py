"""Keep secrets out of every log line: `?token=` / `?access_token=` (any case, percent-encoded keys too) become
`token=***`, and a Doc4AI PAT or `Bearer <secret>` anywhere becomes its redacted form (aidoc.mcp.redact)."""
from __future__ import annotations

import logging
import re
from urllib.parse import unquote

from aidoc.mcp.redact import redact_text

_PARAM_RE = re.compile(r"([?&])([^=&\s]+)=([^&\s]*)")
_SECRET_KEYS = {"token", "access_token"}


def _param(m: re.Match) -> str:
    sep, key, val = m.group(1), m.group(2), m.group(3)
    if unquote(key).lower() in _SECRET_KEYS:
        return f"{sep}{key}=***"
    if "doc4ai" in unquote(val).lower() and redact_text(unquote(val)) != unquote(val):
        return f"{sep}{key}={redact_text(unquote(val))}"
    return m.group(0)


def redact(text: str) -> str:
    if not isinstance(text, str):
        return text
    return redact_text(_PARAM_RE.sub(_param, text))


class RedactTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        _scrub(record)
        return True


def _scrub(record: logging.LogRecord) -> None:
    if isinstance(record.args, tuple) and record.args:
        record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
    elif isinstance(record.args, dict):
        record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
    if isinstance(record.msg, str):
        record.msg = redact(record.msg)


def install_access_log_redaction() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactTokenFilter) for f in logger.filters):
        logger.addFilter(RedactTokenFilter())
    install_secret_redaction()


_installed = False


def install_secret_redaction() -> None:
    """Every log record from every logger (the SDK logs failing resource URIs and tool arguments) is scrubbed when
    it is created, so no handler can see the raw value. Idempotent."""
    global _installed
    if _installed:
        return
    base = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = base(*args, **kwargs)
        _scrub(record)
        return record
    logging.setLogRecordFactory(factory)
    _installed = True
