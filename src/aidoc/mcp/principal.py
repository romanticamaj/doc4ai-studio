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

FailureReason = Literal["missing_token", "bad_format", "bad_checksum", "unknown_token", "revoked", "expired",
                        "wrong_audience"]


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
# the protected-resource metadata URL of the request being served (None when OAuth is off or the Host is unknown);
# set next to current_principal so a forbidden_scope result can carry the step-up challenge (spec 2026-11-05 §4.5)
current_resource_metadata: ContextVar[str | None] = ContextVar("doc4ai_resource_metadata", default=None)


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

    def verify(self, raw: str | None, now: float | None = None, *, resource: str | None = None) -> Principal | AuthFailure:
        """`resource` (the request's canonical /mcp URL) is not used: a PAT is valid on every URL of this server."""
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


class OAuthTokenVerifier:
    """`doc4ai_at_` access tokens (spec 2026-11-05 §7.1): opaque, HMAC-hashed, bound to the grant's resource (the
    /mcp URL the client authorized). The principal's token_id is the grant id: one connection, one identity."""

    def __init__(self, store, secret: bytes, cfg_getter):
        self.store, self.secret, self.cfg = store, secret, cfg_getter

    def verify(self, raw: str | None, now: float | None = None, *, resource: str | None = None) -> Principal | AuthFailure:
        from aidoc.oauth import tokens as OT
        now = time.time() if now is None else now
        if not raw:
            return AuthFailure("missing_token", None)
        seen = OT.prefix_seen(raw)
        cfg = self.cfg()
        if not cfg.oauth.enabled:
            return AuthFailure("unknown_token", seen)
        if len(raw) != len(OT.AT_PREFIX) + OT.BODY_LEN + 1 + OT.CRC_LEN or not raw.startswith(OT.AT_PREFIX):
            return AuthFailure("bad_format", seen)
        if not OT.is_well_formed(OT.AT_PREFIX, raw):
            return AuthFailure("bad_checksum", seen)
        row = self.store.get_oauth_token_by_hash(OT.hash_(self.secret, raw))
        if row is None or row["kind"] != "at":
            return AuthFailure("unknown_token", seen)
        if row["client_id"].startswith("https://") and not cfg.oauth.allow_cimd:
            return AuthFailure("unknown_token", seen)          # CIMD switched off: its connections stop with it
        gid = row["grant_id"]
        if row["revoked_at"] is not None or row["grant_revoked_at"] is not None:
            return AuthFailure("revoked", seen, gid)
        if row["expires_at"] <= now:
            return AuthFailure("expired", seen, gid)
        if resource is None or row["resource"] != resource:
            return AuthFailure("wrong_audience", seen, gid)
        client = self.store.get_oauth_client(row["client_id"])
        return Principal(kind="oauth", subject=row["subject"], token_id=gid, client_id=row["client_id"],
                         scopes=frozenset(row["scopes"]), expires_at=row["expires_at"],
                         name=(client or {}).get("name") or row["client_id"], rate_limit_per_min=cfg.mcp.rate_limit_per_min)


class VerifierChain:
    """Dispatch by prefix (D8): `doc4ai_pat_` -> PatVerifier, `doc4ai_at_` -> OAuthTokenVerifier, anything else is
    answered by the PAT branch (bad_format), with the OAuth prefix seen when it is one of ours."""

    def __init__(self, pat: PatVerifier, oauth: OAuthTokenVerifier):
        self.pat, self.oauth = pat, oauth

    def verify(self, raw: str | None, now: float | None = None, *, resource: str | None = None) -> Principal | AuthFailure:
        from aidoc.oauth import tokens as OT
        if raw and raw.startswith(OT.AT_PREFIX):
            return self.oauth.verify(raw, now, resource=resource)
        result = self.pat.verify(raw, now)
        if isinstance(result, AuthFailure) and result.prefix_seen is None and raw:
            return AuthFailure(result.reason, OT.prefix_seen(raw), result.token_id)
        return result
