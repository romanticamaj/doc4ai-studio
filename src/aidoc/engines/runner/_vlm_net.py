"""Loopback test and secret redaction for the VLM engine. STDLIB ONLY: used by vlm_runner.py (host interpreter, no
aidoc imports) and by aidoc.engines.vlm / aidoc.setup_engines, so there is one rule for both sides. The web's
isLoopbackUrl.ts follows the same table (web/src/features/settings/__tests__/loopbackCases.json)."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit


def is_loopback_host(host: str | None) -> bool:
    """A hostname (no port, no brackets) that is this computer: "localhost" or a loopback IP, IPv4-mapped included.
    0.0.0.0 is not (it is "any interface", not an address a client should call local)."""
    h = (host or "").strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool((mapped or ip).is_loopback)


def is_loopback_url(url: str) -> bool:
    try:
        return is_loopback_host(urlsplit((url or "").strip()).hostname)
    except ValueError:
        return False


_BEARER = re.compile(r"(?i)\bBearer\s+[^\s\"',;]+")
_SK = re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}")


def redact(text: str, secrets=()) -> str:
    """`text` without API keys: the given secrets, `Bearer <token>` and `sk-…` patterns become ***."""
    s = str(text)
    for secret in secrets:
        if secret and len(secret) >= 4:
            s = s.replace(secret, "***")
    s = _BEARER.sub("Bearer ***", s)
    return _SK.sub("***", s)
