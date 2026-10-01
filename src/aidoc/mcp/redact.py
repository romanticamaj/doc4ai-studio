"""One redaction rule for everything the MCP server writes down or shows: call-log args and resource URIs, error
text, SSE payloads and log lines. A Doc4AI PAT anywhere becomes `doc4ai_pat_<redacted:abcd>` (the display prefix
only), any other `Bearer <secret>` becomes `Bearer <redacted>`, and long base64-looking strings under any key become
`<base64 len=N sha256=xxxxxxxx>` (never stored, still identifiable)."""
from __future__ import annotations

import hashlib
import re
from typing import Any

_PAT_RE = re.compile(r"doc4ai_pat_([A-Za-z0-9]{1,4})([A-Za-z0-9]*(?:_[A-Za-z0-9]*)?)")
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+(?!<redacted)[A-Za-z0-9._~+/=-]{8,}")
_B64_RE = re.compile(r"^(?:data:[^,]*;base64,)?[A-Za-z0-9+/=_\-\r\n]+$")       # no spaces: prose is not a blob
B64_MIN = 200


def redact_text(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    if "doc4ai_pat_" in text:
        # a bare display prefix (doc4ai_pat_ + 4 chars, what the admin UI shows) is not secret and stays readable
        text = _PAT_RE.sub(lambda m: m.group(0) if not m.group(2) else f"doc4ai_pat_<redacted:{m.group(1)}>", text)
    if "earer" in text or "EARER" in text:
        text = _BEARER_RE.sub(lambda m: f"{m.group(1)} <redacted>", text)
    return text


def blob_summary(s: str) -> str:
    return f"<base64 len={len(s)} sha256={hashlib.sha256(s.encode('utf-8')).hexdigest()[:8]}>"


def redact_value(value: Any) -> Any:
    """Recursively: strings redacted (and summarised when they are long base64), containers walked."""
    if isinstance(value, str):
        if len(value) > B64_MIN and _B64_RE.match(value):
            return blob_summary(value)
        return redact_text(value)
    if isinstance(value, dict):
        return {redact_text(str(k)) if isinstance(k, str) else k: redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    return value
