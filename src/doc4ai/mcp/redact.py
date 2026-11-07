"""One redaction rule for everything the MCP server writes down or shows: call-log args and resource URIs, error
text, SSE payloads and log lines. A Doc4AI secret anywhere — PAT, OAuth access / refresh token, authorization code,
client id — becomes `doc4ai_<kind>_<redacted:abcd>` (the display prefix only), any other `Bearer <secret>` becomes
`Bearer <redacted>`, the values of the OAuth form / query fields `code`, `code_verifier`, `refresh_token`,
`access_token`, `client_secret` become `<redacted>`, and long base64-looking strings under any key become
`<base64 len=N sha256=xxxxxxxx>` (never stored, still identifiable)."""
from __future__ import annotations

import hashlib
import re
from typing import Any

_SECRET_RE = re.compile(r"doc4ai_(pat|at|rt|ac|cl)_([A-Za-z0-9]{1,4})([A-Za-z0-9]*(?:_[A-Za-z0-9]*)?)")
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+(?!<redacted)[A-Za-z0-9._~+/=-]{8,}")
_B64_RE = re.compile(r"^(?:data:[^,]*;base64,)?[A-Za-z0-9+/=_\-\r\n]+$")       # no spaces: prose is not a blob
B64_MIN = 200
# OAuth secrets that travel as form / query fields (spec 2026-11-05 §5.4). In a dict, "code" is NOT redacted by its
# key: MCP error payloads use "code" for the error name (forbidden_scope, ...); an authorization code is still caught
# by its doc4ai_ac_ prefix.
_SECRET_FORM_KEYS = ("code", "code_verifier", "refresh_token", "access_token", "client_secret")
_SECRET_DICT_KEYS = frozenset(_SECRET_FORM_KEYS) - {"code"}
_FORM_RE = re.compile(r"(?:^|(?<=[?&\s]))(" + "|".join(_SECRET_FORM_KEYS) + r")=(?!<redacted>)[^&\s\"']+")


def redact_text(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    if "=" in text:
        text = _FORM_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    if "doc4ai_" in text:
        # a bare display prefix (doc4ai_<kind>_ + 4 chars, what the admin UI shows) is not secret and stays readable
        text = _SECRET_RE.sub(lambda m: m.group(0) if not m.group(3) else f"doc4ai_{m.group(1)}_<redacted:{m.group(2)}>",
                              text)
    if "earer" in text or "EARER" in text:
        text = _BEARER_RE.sub(lambda m: f"{m.group(1)} <redacted>", text)
    return text


def blob_summary(s: str) -> str:
    return f"<base64 len={len(s)} sha256={hashlib.sha256(s.encode('utf-8')).hexdigest()[:8]}>"


def redact_value(value: Any) -> Any:
    """Recursively: strings redacted (and summarised when they are long base64), containers walked; the values of
    the OAuth secret keys (refresh_token, access_token, code_verifier, client_secret) replaced outright."""
    if isinstance(value, str):
        if len(value) > B64_MIN and _B64_RE.match(value):
            return blob_summary(value)
        return redact_text(value)
    if isinstance(value, dict):
        return {redact_text(str(k)) if isinstance(k, str) else k:
                ("<redacted>" if isinstance(k, str) and k.lower() in _SECRET_DICT_KEYS and v is not None else redact_value(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    return value
