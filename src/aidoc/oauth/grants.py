"""Authorization codes, grants, access and refresh tokens (spec 2026-11-05 §5.3).

One grant = one connection of one client. Codes are single-use and short-lived; a reused code revokes what the first
exchange created. Refresh tokens rotate; presenting a rotated one revokes the whole grant (RFC 9700 §4.14.2).
`Principal.token_id` for OAuth is the grant id, so a connection keeps one identity across refreshes (spec §7.1)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from aidoc.oauth import tokens as T

GRANT_REVOKED_KEEP_DAYS = 30         # revoked grants stay visible (已撤銷) this long, then go with their tokens
TOKEN_EXPIRED_KEEP_DAYS = 90         # expired / revoked token rows are kept this long
CODE_CONSUMED_KEEP_S = 3600          # a consumed code is kept this long so a reuse can be detected
SUBJECT = "owner"                    # single-user server (spec §1.4)
DAY = 86400
_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
CHALLENGE_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
BAD_CODE = "authorization code is invalid, expired or already used"
BAD_REFRESH = "refresh token is invalid, expired or revoked"


class OAuthError(Exception):
    """RFC 6749 §5.2 error: rendered as 400 {error, error_description}."""

    def __init__(self, error: str, description: str, status: int = 400):
        super().__init__(f"{error}: {description}")
        self.error, self.description, self.status = error, description, status


@dataclass
class TokenResponse:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str
    grant_id: str

    def body(self) -> dict:
        return {"access_token": self.access_token, "token_type": "Bearer", "expires_in": self.expires_in,
                "refresh_token": self.refresh_token, "scope": self.scope}


def verify_pkce(verifier, challenge: str) -> bool:
    if not isinstance(verifier, str) or not _VERIFIER_RE.match(verifier):
        return False
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(digest, challenge)


def cimd_client(client_id: str) -> bool:
    """A client identified by its metadata document URL (CIMD), as opposed to a DCR registration."""
    return client_id.startswith("https://")


def source_label(client: dict | None, client_id: str) -> str:
    """Where a connection comes from, as the admin shows it: the CIMD document's host (claude.ai) or the DCR id's
    display prefix (doc4ai_cl_xxxx)."""
    if client_id.startswith("https://"):
        return (urlsplit(client_id).hostname or client_id).lower()
    return T.display_prefix(client_id)


def _scope_str(scopes) -> str:
    from aidoc.mcp.principal import SCOPES
    return " ".join(s for s in SCOPES if s in set(scopes))


def _need(form: dict, key: str) -> str:
    v = form.get(key)
    if not isinstance(v, str) or not v:
        raise OAuthError("invalid_request", f"missing {key}")
    return v


class GrantService:
    def __init__(self, ctx):
        self.ctx = ctx

    @property
    def store(self):
        return self.ctx.store

    @property
    def secret(self) -> bytes:
        return self.ctx.extras["mcp"].secret

    def hash(self, raw: str) -> str:
        return T.hash_(self.secret, raw)

    # ---- authorization codes
    def issue_code(self, client: dict, redirect_uri: str, code_challenge: str, scopes, resource: str, ip, now: float) -> str:
        raw = T.generate(T.CODE_PREFIX)
        self.store.create_oauth_code(code_hash=self.hash(raw), client_id=client["client_id"], redirect_uri=redirect_uri,
                                     code_challenge=code_challenge, scopes=list(scopes), resource=resource, subject=SUBJECT,
                                     expires_at=now + self.ctx.config.oauth.code_ttl_s, created_at=now)
        return raw

    def _mint(self, grant_id: str, now: float, absolute: float) -> tuple[str, str, float]:
        """A new access token row; returns (raw access token, refresh expiry, access expiry)."""
        o = self.ctx.config.oauth
        at = T.generate(T.AT_PREFIX)
        at_exp = now + o.access_ttl_min * 60
        self.store.create_oauth_token(grant_id=grant_id, kind="at", token_hash=self.hash(at), prefix=T.display_prefix(at),
                                      expires_at=at_exp, created_at=now)
        return at, min(now + o.refresh_ttl_days * DAY, absolute), at_exp

    def exchange_code(self, form: dict, ip, now: float, *, resource: str | None = None) -> TokenResponse:
        """grant_type=authorization_code. `resource` is this request's canonical resource (unused for the check: a
        `resource` parameter, when given, must equal the one the code was issued for)."""
        code, verifier = _need(form, "code"), _need(form, "code_verifier")
        redirect_uri, client_id = _need(form, "redirect_uri"), _need(form, "client_id")
        store = self.store
        with store.con.lock:
            row = store.take_oauth_code(self.hash(code), now) if T.is_well_formed(T.CODE_PREFIX, code) else None
            if row is None:
                raise OAuthError("invalid_grant", BAD_CODE)
            if row["consumed_at"] is not None:
                if row.get("grant_id"):
                    self.revoke_grant(row["grant_id"], "code_reuse", now)
                raise OAuthError("invalid_grant", BAD_CODE)
            if row["expires_at"] <= now or row["client_id"] != client_id or row["redirect_uri"] != redirect_uri:
                raise OAuthError("invalid_grant", BAD_CODE)
            if form.get("resource") is not None and form.get("resource") != row["resource"]:
                raise OAuthError("invalid_target", "resource does not match the authorization request")
            if not verify_pkce(verifier, row["code_challenge"]):
                raise OAuthError("invalid_grant", "code_verifier does not match the code_challenge")
            o = self.ctx.config.oauth
            absolute = now + o.refresh_max_days * DAY
            gid = store.create_oauth_grant(client_id=client_id, subject=row["subject"], scopes=row["scopes"],
                                           resource=row["resource"], created_ip=ip, absolute_expires_at=absolute, created_at=now,
                                           redirect_uri=row["redirect_uri"])
            store.bind_oauth_code(row["code_hash"], gid)
            at, rt_exp, _ = self._mint(gid, now, absolute)
            rt = T.generate(T.RT_PREFIX)
            store.create_oauth_token(grant_id=gid, kind="rt", token_hash=self.hash(rt), prefix=T.display_prefix(rt),
                                     expires_at=rt_exp, created_at=now)
        store.touch_oauth_client(client_id, now)
        self._publish(gid, "created")
        return TokenResponse(at, rt, o.access_ttl_min * 60, _scope_str(row["scopes"]), gid)

    def refresh(self, form: dict, ip, now: float, *, resource: str | None = None) -> TokenResponse:
        raw, client_id = _need(form, "refresh_token"), _need(form, "client_id")
        store = self.store
        with store.con.lock:
            row = store.get_oauth_token_by_hash(self.hash(raw)) if T.is_well_formed(T.RT_PREFIX, raw) else None
            if row is None or row["kind"] != "rt" or row["revoked_at"] is not None or row["grant_revoked_at"] is not None:
                raise OAuthError("invalid_grant", BAD_REFRESH)
            if row["rotated_at"] is not None:
                self.revoke_grant(row["grant_id"], "refresh_replay", now)
                raise OAuthError("invalid_grant", BAD_REFRESH)
            if row["expires_at"] <= now or row["absolute_expires_at"] <= now or row["client_id"] != client_id:
                raise OAuthError("invalid_grant", BAD_REFRESH)
            if cimd_client(row["client_id"]) and not self.ctx.config.oauth.allow_cimd:   # switched off: no new tokens
                raise OAuthError("invalid_grant", BAD_REFRESH)
            if form.get("resource") is not None and form.get("resource") != row["resource"]:
                raise OAuthError("invalid_target", "resource does not match the grant")
            scopes = row["scopes"]
            if form.get("scope") is not None:
                asked = form["scope"].split()
                if not asked or not set(asked) <= set(scopes):
                    raise OAuthError("invalid_scope", "a refresh can only keep or narrow the granted scopes")
                if set(asked) != set(scopes):                  # narrowing is permanent for the connection
                    scopes = [s for s in scopes if s in set(asked)]
                    store.con.execute("UPDATE oauth_grants SET scopes_json=? WHERE id=?",
                                      (json.dumps(scopes), row["grant_id"]))
            at, rt_exp, _ = self._mint(row["grant_id"], now, row["absolute_expires_at"])
            rt = T.generate(T.RT_PREFIX)
            if store.rotate_oauth_refresh(row["id"], new_hash=self.hash(rt), new_prefix=T.display_prefix(rt),
                                          expires_at=rt_exp, at=now) is None:
                self.revoke_grant(row["grant_id"], "refresh_replay", now)
                raise OAuthError("invalid_grant", BAD_REFRESH)
        return TokenResponse(at, rt, self.ctx.config.oauth.access_ttl_min * 60, _scope_str(scopes), row["grant_id"])

    # ---- revocation
    def revoke_by_token(self, raw, now: float) -> bool:
        if not any(T.is_well_formed(p, raw) for p in (T.AT_PREFIX, T.RT_PREFIX)):
            return False
        row = self.store.get_oauth_token_by_hash(self.hash(raw))
        if row is None:
            return False
        self.revoke_grant(row["grant_id"], "client_revoke", now)
        return True

    def revoke_grant(self, grant_id: str, reason: str, now: float) -> int:
        """Every AT and RT of the grant; the next /mcp request with any of them is 401. Returns tokens marked."""
        g = self.store.get_oauth_grant(grant_id)
        if g is None or g["revoked_at"] is not None:
            return 0
        n = self.store.revoke_oauth_grant(grant_id, reason, at=now)
        self._publish(grant_id, "revoked")
        return n

    def _publish(self, grant_id: str, action: str) -> None:
        g = self.store.get_oauth_grant(grant_id)
        if g is None or self.ctx.bus is None:
            return
        client = self.store.get_oauth_client(g["client_id"])
        self.ctx.bus.publish("mcp.token", grant_id, {"id": grant_id, "name": (client or {}).get("name") or g["client_id"],
                                                     "prefix": source_label(client, g["client_id"]), "action": action,
                                                     "kind": "oauth"})
