"""Who is calling (MCP spec §2.1). Every verifier produces a `Principal`; tools, scope checks and logs see only it."""
from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from aidoc.mcp import tokens as T

SCOPE_READ = "doc4ai:read"
SCOPE_CONVERT = "doc4ai:convert"
SCOPE_CONVERT_LOCAL = "doc4ai:convert:local"
SCOPE_MANAGE = "doc4ai:manage"
SCOPES = (SCOPE_READ, SCOPE_CONVERT, SCOPE_CONVERT_LOCAL, SCOPE_MANAGE)

FailureReason = Literal["missing_token", "bad_format", "bad_checksum", "unknown_token", "revoked", "expired"]


@dataclass(frozen=True)
class Principal:
    kind: Literal["pat", "oauth", "admin"]
    subject: str                    # pat: "owner" (Phase 1); oauth: sub
    token_id: str | None
    client_id: str | None           # oauth client_id (Phase 2)
    scopes: frozenset[str]
    expires_at: float | None
    name: str = ""                  # api_tokens.name, for logs and events
    rate_limit_per_min: int | None = None

    def has(self, scope: str) -> bool:
        return scope in self.scopes


@dataclass(frozen=True)
class AuthFailure:
    reason: FailureReason
    prefix_seen: str | None
    token_id: str | None = None          # known-but-revoked/expired token: lets the log show its name


current_principal: ContextVar[Principal | None] = ContextVar("doc4ai_principal", default=None)


def parse_bearer(header_value: str | None) -> str | None:
    """The token in `Authorization: Bearer <t>`; scheme case-insensitive, whitespace and quotes stripped."""
    if not header_value:
        return None
    parts = header_value.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    tok = parts[1].strip().strip('"').strip()
    return tok or None


class PatVerifier:
    def __init__(self, store, secret: bytes):
        self.store = store
        self.secret = secret

    def verify(self, raw: str | None, now: float | None = None) -> Principal | AuthFailure:
        now = time.time() if now is None else now
        if not raw:
            return AuthFailure("missing_token", None)
        seen = T.prefix_seen(raw)
        if len(raw) != T.TOKEN_LEN or not raw.startswith(T.PREFIX):
            return AuthFailure("bad_format", seen)
        if not T.is_well_formed(raw):
            return AuthFailure("bad_checksum", seen)
        row = self.store.get_api_token_by_hash(T.token_hash(self.secret, raw))
        if row is None:
            return AuthFailure("unknown_token", seen)
        if row["revoked_at"] is not None and row["revoked_at"] <= now:
            return AuthFailure("revoked", seen, row["id"])
        if row["expires_at"] is not None and row["expires_at"] <= now:
            return AuthFailure("expired", seen, row["id"])
        return Principal(kind="pat", subject="owner", token_id=row["id"], client_id=None,
                         scopes=frozenset(row["scopes"]), expires_at=row["expires_at"], name=row["name"],
                         rate_limit_per_min=row["rate_limit_per_min"])
