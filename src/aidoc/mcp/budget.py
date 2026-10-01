"""Response budget (MCP spec §5.1: default 8,000 tokens, truncate at a boundary) and opaque cursors (D10)."""
from __future__ import annotations

import base64
import json

from aidoc import chunk as _chunk


class CursorError(ValueError):
    pass


def estimate_tokens(text: str) -> tuple[int, str]:
    try:
        return _chunk.count_tokens(text), "tiktoken"
    except _chunk.TokenizerUnavailable:
        return len(text.encode("utf-8")) // 3, "bytes"


def _fits(text: str, max_tokens: int) -> bool:
    return estimate_tokens(text)[0] <= max_tokens


def cut_to_budget(text: str, max_tokens: int, *, start: int = 0) -> tuple[str, int | None]:
    """text[start:] cut to ~max_tokens at the last paragraph break (then line break, then space) before the limit.
    Binary search on the character length (token counting is monotone enough for this purpose)."""
    body = text[start:]
    if not body:
        return "", None
    if _fits(body, max_tokens):
        return body, None
    lo, hi = 1, len(body)                     # largest prefix length that fits
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _fits(body[:mid], max_tokens):
            lo = mid
        else:
            hi = mid - 1
    limit = lo
    cut = -1
    for sep in ("\n\n", "\n", " "):
        cut = body.rfind(sep, 0, limit)
        if cut > limit // 2:                  # a boundary in the second half of the window is worth using
            break
        cut = -1
    if cut <= 0:
        cut = limit
    kept = body[:cut].rstrip()
    if not kept:
        kept = body[:limit]
        cut = limit
    return kept, start + cut


def encode_cursor(d: dict) -> str:
    raw = json.dumps(d, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(s: str | None) -> dict | None:
    if not s:
        return None
    try:
        raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        d = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise CursorError("invalid cursor") from e
    if not isinstance(d, dict):
        raise CursorError("invalid cursor")
    return d
