"""Admin API for the MCP server (MCP spec §7): tokens, status, clients, calls, stats, snippets. Server token /
loopback only (the `api` router's require_token); PATs are never valid here."""
from __future__ import annotations

import time

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from aidoc.mcp import tokens as T
from aidoc.mcp.principal import SCOPE_READ, SCOPES
from aidoc.mcp.server import PROTOCOL_VERSIONS, SDK_VERSION, endpoint_urls, transport_security_for
from aidoc.server.auth import ApiError

router = APIRouter()
EXPIRING_SOON_S = 14 * 86400
DAY = 86400


class TokenCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(min_length=1)
    expires_in_days: int | None = Field(default=None, ge=0, le=3650)
    note: str | None = Field(default=None, max_length=500)
    rate_limit_per_min: int | None = Field(default=None, ge=1, le=100000)


class TokenPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=500)
    rate_limit_per_min: int | None = Field(default=None, ge=1, le=100000)


class RevokeIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


def _ctx(request: Request):
    return request.app.state.ctx


def _runtime(ctx):
    return ctx.extras["mcp"]


def token_status(row: dict, now: float) -> str:
    if row["revoked_at"] is not None and row["revoked_at"] <= now:
        return "revoked"
    if row["expires_at"] is not None and row["expires_at"] <= now:
        return "expired"
    return "active"


def serialize_token(store, row: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    out = {k: v for k, v in row.items() if k not in ("token_hash", "workspace")}
    out["status"] = token_status(row, now)
    out["calls_24h"] = store.count_mcp_calls(since=now - DAY, token_id=row["id"])
    out["errors_24h"] = store.count_mcp_calls(since=now - DAY, token_id=row["id"], errors_only=True)
    return out


def _check_scopes(scopes: list[str]) -> list[str]:
    s = list(dict.fromkeys(scopes))
    if any(x not in SCOPES for x in s) or SCOPE_READ not in s:
        raise ApiError(422, "invalid_scopes", allowed=list(SCOPES), detail="every token needs doc4ai:read")
    return [x for x in SCOPES if x in s]                        # canonical order


def _ttl_seconds(cfg, expires_in_days: int | None) -> float | None:
    days = cfg.mcp.default_token_ttl_days if expires_in_days is None else expires_in_days
    if days == 0:
        if not cfg.mcp.allow_no_expiry:
            raise ApiError(422, "no_expiry_disabled", detail="set mcp.allow_no_expiry = true to issue tokens without expiry")
        return None
    if days > cfg.mcp.max_token_ttl_days:
        raise ApiError(422, "ttl_too_long", max=cfg.mcp.max_token_ttl_days)
    return days * DAY


def issue_token(ctx, *, name, scopes, expires_in_days, note, rate_limit_per_min, rotated_from=None,
                ttl_seconds: float | None = None, use_ttl: bool = False) -> tuple[str, dict]:
    ttl = ttl_seconds if use_ttl else _ttl_seconds(ctx.config, expires_in_days)
    raw = T.generate_token()
    tid = ctx.store.create_api_token(name=name, prefix=T.display_prefix(raw), token_hash=T.token_hash(_runtime(ctx).secret, raw),
                                     scopes=scopes, expires_at=None if ttl is None else time.time() + ttl, note=note,
                                     rate_limit_per_min=rate_limit_per_min, rotated_from=rotated_from)
    return raw, ctx.store.get_api_token(tid)


def _publish_token(ctx, row: dict, action: str) -> None:
    ctx.bus.publish("mcp.token", row["id"], {"id": row["id"], "name": row["name"], "prefix": row["prefix"], "action": action})


def _snippets_for(ctx, token: str) -> list[dict]:
    try:
        from aidoc.mcp.snippets import render_snippets  # Task 27
    except ImportError:
        return []
    urls = endpoint_urls(ctx.config, str(ctx.extras.get("bind_host") or ctx.config.server.host),
                         int(ctx.extras.get("bind_port") or ctx.config.server.port))
    return render_snippets(urls[-1] if len(urls) > 1 else urls[0], token)


@router.get("/mcp/status")
def status(request: Request) -> dict:
    ctx = _ctx(request)
    cfg, now = ctx.config, time.time()
    bind_host = str(ctx.extras.get("bind_host") or cfg.server.host)
    port = int(ctx.extras.get("bind_port") or cfg.server.port)
    expiring = sum(1 for t in ctx.store.list_api_tokens()
                   if token_status(t, now) == "active" and t["expires_at"] is not None and t["expires_at"] - now < EXPIRING_SOON_S)
    return {"enabled": cfg.mcp.enabled, "endpoint_urls": endpoint_urls(cfg, bind_host, port),
            "bind": {"host": bind_host, "port": port},
            "allowed_hosts": transport_security_for(cfg, bind_host, port).allowed_hosts,
            "protocol_versions": PROTOCOL_VERSIONS, "sdk_version": SDK_VERSION,
            "active_clients": len(ctx.store.list_mcp_clients(active_since=now - 300)),
            "calls_24h": ctx.store.count_mcp_calls(since=now - DAY),
            "errors_24h": ctx.store.count_mcp_calls(since=now - DAY, errors_only=True),
            "tokens_expiring_soon": expiring, "local_path_roots": list(cfg.mcp.local_path_roots),
            "plaintext_http": True, "tokenizer": ctx.store.page_index_tokenizer()}


@router.get("/mcp/tokens")
def list_tokens(request: Request) -> dict:
    store = _ctx(request).store
    now = time.time()
    return {"tokens": [serialize_token(store, t, now) for t in store.list_api_tokens()]}


@router.post("/mcp/tokens", status_code=201)
def create_token(body: TokenCreateIn, request: Request) -> dict:
    ctx = _ctx(request)
    scopes = _check_scopes(body.scopes)
    raw, row = issue_token(ctx, name=body.name, scopes=scopes, expires_in_days=body.expires_in_days, note=body.note,
                           rate_limit_per_min=body.rate_limit_per_min)
    _publish_token(ctx, row, "created")
    return {"token": raw, "record": serialize_token(ctx.store, row), "snippets": _snippets_for(ctx, raw)}


@router.patch("/mcp/tokens/{token_id}")
async def patch_token(token_id: str, request: Request) -> dict:
    ctx = _ctx(request)
    row = ctx.store.get_api_token(token_id)
    if row is None:
        raise ApiError(404, "not_found")
    try:
        data = await request.json()
    except ValueError:
        raise ApiError(422, "validation_error", detail="body is not JSON") from None
    if isinstance(data, dict) and "scopes" in data:
        raise ApiError(422, "scopes_immutable", detail="rotate or create a new token to change scopes")
    body = TokenPatchIn.model_validate(data)
    fields = body.model_dump(exclude_unset=True)
    if fields:
        ctx.store.update_api_token(token_id, **fields)
    row = ctx.store.get_api_token(token_id)
    _publish_token(ctx, row, "updated")
    return {"token": serialize_token(ctx.store, row)}


@router.post("/mcp/tokens/{token_id}/revoke")
def revoke_token(token_id: str, request: Request, body: RevokeIn | None = None) -> dict:
    ctx = _ctx(request)
    if ctx.store.get_api_token(token_id) is None:
        raise ApiError(404, "not_found")
    if not ctx.store.revoke_api_token(token_id, (body or RevokeIn()).reason, at=time.time()):
        raise ApiError(409, "already_revoked")
    row = ctx.store.get_api_token(token_id)
    _publish_token(ctx, row, "revoked")
    return {"token": serialize_token(ctx.store, row)}


@router.post("/mcp/tokens/{token_id}/rotate", status_code=201)
def rotate_token(token_id: str, request: Request) -> dict:
    ctx = _ctx(request)
    old = ctx.store.get_api_token(token_id)
    if old is None:
        raise ApiError(404, "not_found")
    if token_status(old, time.time()) == "revoked":
        raise ApiError(409, "already_revoked")
    ttl = None if old["expires_at"] is None else max(DAY, old["expires_at"] - old["created_at"])
    raw, new = issue_token(ctx, name=old["name"], scopes=old["scopes"], expires_in_days=None, note=old["note"],
                           rate_limit_per_min=old["rate_limit_per_min"], rotated_from=old["id"], ttl_seconds=ttl, use_ttl=True)
    ctx.store.revoke_api_token(token_id, "rotated", at=time.time())            # D12: no grace in Phase 1
    _publish_token(ctx, new, "rotated")
    return {"token": raw, "record": serialize_token(ctx.store, new), "snippets": _snippets_for(ctx, raw),
            "revoked": serialize_token(ctx.store, ctx.store.get_api_token(token_id))}
