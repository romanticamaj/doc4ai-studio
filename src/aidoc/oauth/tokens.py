"""OAuth secret formats (spec 2026-11-05 §5.4), built on the PAT format of `aidoc.mcp.tokens`:
`doc4ai_at_` / `doc4ai_rt_` / `doc4ai_ac_` + 43 base62 + `_` + 6 base62 CRC32 (access token, refresh token,
authorization code), and `doc4ai_cl_` + 22 base62 for DCR client ids (public, no checksum). Only
HMAC-SHA256(server_secret, value) is ever stored."""
from __future__ import annotations

import hmac
import secrets

from aidoc.mcp import tokens as _pat

AT_PREFIX = "doc4ai_at_"
RT_PREFIX = "doc4ai_rt_"
CODE_PREFIX = "doc4ai_ac_"
CLIENT_PREFIX = "doc4ai_cl_"
CLIENT_ID_LEN = 22
BODY_LEN = _pat.BODY_LEN
CRC_LEN = _pat.CRC_LEN
SECRET_PREFIXES = (AT_PREFIX, RT_PREFIX, CODE_PREFIX)
ALL_PREFIXES = (_pat.PREFIX, *SECRET_PREFIXES, CLIENT_PREFIX)

hash_ = _pat.token_hash


def generate(prefix: str) -> str:
    body = _pat.b62encode(int.from_bytes(secrets.token_bytes(32), "big"), BODY_LEN)
    return f"{prefix}{body}_{_pat.crc_b62(body)}"


def generate_client_id() -> str:
    return CLIENT_PREFIX + "".join(secrets.choice(_pat.ALPHABET) for _ in range(CLIENT_ID_LEN))


def is_well_formed(prefix: str, raw) -> bool:
    if not isinstance(raw, str) or len(raw) != len(prefix) + BODY_LEN + 1 + CRC_LEN or not raw.startswith(prefix):
        return False
    rest = raw[len(prefix):]
    if rest[BODY_LEN] != "_":
        return False
    body, crc = rest[:BODY_LEN], rest[BODY_LEN + 1:]
    if not (set(body) <= _pat._ALPHA and set(crc) <= _pat._ALPHA):
        return False
    return hmac.compare_digest(_pat.crc_b62(body), crc)


def display_prefix(raw: str) -> str:
    for p in ALL_PREFIXES:
        if raw.startswith(p):
            return raw[: len(p) + 4]
    return raw[:4]


def prefix_seen(raw) -> str | None:
    """What a failed attempt is logged as: a known prefix plus 4 characters, never more."""
    if isinstance(raw, str):
        for p in ALL_PREFIXES:
            if raw.startswith(p):
                return raw[: len(p) + 4]
    return None
