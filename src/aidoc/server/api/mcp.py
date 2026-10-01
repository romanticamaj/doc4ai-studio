"""Admin API for the MCP server (MCP spec §7): tokens, status, clients, calls, stats, snippets. Server token /
loopback only (the `api` router's require_token); PATs are never valid here."""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aidoc.mcp import tokens as T
from aidoc.mcp.middleware import TOOL_SCOPES
from aidoc.mcp.principal import SCOPE_READ, SCOPES
from aidoc.mcp.server import PROTOCOL_VERSIONS, SDK_VERSION, endpoint_urls, transport_security_for
from aidoc.mcp.snippets import render_snippets
from aidoc.server.auth import ApiError

router = APIRouter()
EXPIRING_SOON_S = 14 * 86400
UNKNOWN_TOOL = "(unknown)"
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


def _default_endpoint(ctx) -> tuple[list[str], str]:
    urls = endpoint_urls(ctx.config, str(ctx.extras.get("bind_host") or ctx.config.server.host),
                         int(ctx.extras.get("bind_port") or ctx.config.server.port))
    public = [u for u in urls if not any(h in u for h in ("127.0.0.1", "localhost", "[::1]"))]
    return urls, (public[-1] if public else urls[0])


@router.get("/mcp/config-snippets")
def config_snippets(request: Request, token_id: str | None = None, endpoint: str | None = None) -> dict:
    ctx = _ctx(request)
    if token_id is not None and ctx.store.get_api_token(token_id) is None:
        raise ApiError(404, "not_found")
    urls, chosen = _default_endpoint(ctx)
    if endpoint is not None:
        if endpoint not in urls:
            raise ApiError(422, "unknown_endpoint", endpoint_urls=urls)
        chosen = endpoint
    return {"endpoint_url": chosen, "snippets": render_snippets(chosen)}


def _snippets_for(ctx, token: str) -> list[dict]:
    return render_snippets(_default_endpoint(ctx)[1], token)


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
            "plaintext_http": True, "tokenizer": ctx.store.page_index_tokenizer(),
            "config_warnings": list(getattr(cfg, "warnings", []))}


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
    if isinstance(data, dict) and "name" in data and data["name"] is None:
        raise ApiError(422, "validation_error", detail="name cannot be null")
    try:
        body = TokenPatchIn.model_validate(data)
    except ValidationError as e:
        raise ApiError(422, "validation_error", detail=json.loads(e.json(include_url=False))) from None
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


ACTIVE_WINDOW_S = 300


def serialize_client(store, row: dict, now: float, token_names: dict | None = None) -> dict:
    names = token_names if token_names is not None else {}
    tname = names.get(row["token_id"]) if row.get("token_id") else None
    if tname is None and row.get("token_id"):
        t = store.get_api_token(row["token_id"])
        tname = t["name"] if t else None
    return {**row, "token_name": tname, "active": (now - row["last_seen"]) <= ACTIVE_WINDOW_S}


@router.get("/mcp/clients")
def list_clients(request: Request, active: int = 0, token_id: str | None = None) -> dict:
    store = _ctx(request).store
    now = time.time()
    rows = store.list_mcp_clients(token_id=token_id, active_since=now - ACTIVE_WINDOW_S if active else None)
    names = {t["id"]: t["name"] for t in store.list_api_tokens()}
    return {"clients": [serialize_client(store, r, now, names) for r in rows]}


def serialize_call(row: dict, token_names: dict, client_names: dict) -> dict:
    return {**row, "token_name": token_names.get(row["token_id"]), "client_name": client_names.get(row["client_id"])}


@router.get("/mcp/calls")
def list_calls(request: Request, token_id: str | None = None, client_id: str | None = None, tool: str | None = None,
               status: str | None = None, since: float | None = None, until: float | None = None, cursor: str | None = None,
               limit: int = 50) -> dict:
    store = _ctx(request).store
    if not 1 <= limit <= 100:
        raise ApiError(422, "validation_error", detail="limit must be 1..100")
    before = None
    if cursor:
        if not cursor.isdigit():
            raise ApiError(422, "invalid_cursor")
        before = int(cursor)
    rows = store.list_mcp_calls(token_id=token_id, client_id=client_id, tool=tool, status=status, since=since, until=until,
                                before_id=before, limit=limit + 1)
    more = len(rows) > limit
    rows = rows[:limit]
    token_names = {t["id"]: t["name"] for t in store.list_api_tokens()}
    client_names = {c["id"]: c["client_name"] for c in store.list_mcp_clients()}
    return {"calls": [serialize_call(r, token_names, client_names) for r in rows],
            "next_cursor": str(rows[-1]["id"]) if more and rows else None}


def percentile(values: list[int], p: float) -> int | None:
    if not values:
        return None
    s = sorted(values)
    idx = max(0, min(len(s) - 1, round(p * (len(s) - 1))))
    return int(s[idx])


def stats(store, since: float, bucket_s: float, buckets: int) -> dict:
    rows = store.mcp_call_rows_since(since)
    per_tool: dict[str, dict] = {}
    per_token: dict[str, dict] = {}
    series = [{"ts": since + i * bucket_s, "calls": 0, "errors": 0} for i in range(buckets)]
    for r in rows:
        err = r["status"] != "ok"
        if r.get("tool_name"):
            name = r["tool_name"] if r["tool_name"] in TOOL_SCOPES else UNKNOWN_TOOL     # junk names share one row
            t = per_tool.setdefault(name, {"calls": 0, "errors": 0, "durations": [], "tokens": []})
            t["calls"] += 1
            t["errors"] += int(err)
            if r.get("duration_ms") is not None:
                t["durations"].append(int(r["duration_ms"]))
            if r.get("response_tokens_est") is not None:
                t["tokens"].append(int(r["response_tokens_est"]))
        if r.get("token_id"):
            k = per_token.setdefault(r["token_id"], {"calls": 0, "errors": 0})
            k["calls"] += 1
            k["errors"] += int(err)
        i = min(buckets - 1, max(0, int((r["ts"] - since) // bucket_s)))
        series[i]["calls"] += 1
        series[i]["errors"] += int(err)
    names = {t["id"]: t["name"] for t in store.list_api_tokens()}
    tools = [{"tool": name, "calls": v["calls"], "errors": v["errors"], "error_rate": round(v["errors"] / v["calls"], 4),
              "p50_ms": percentile(v["durations"], 0.5), "p95_ms": percentile(v["durations"], 0.95),
              "tokens_median": percentile(v["tokens"], 0.5)} for name, v in sorted(per_tool.items())]
    tokens = [{"token_id": k, "name": names.get(k), "calls": v["calls"], "errors": v["errors"]}
              for k, v in sorted(per_token.items(), key=lambda kv: -kv[1]["calls"])]
    return {"tools": tools, "tokens": tokens, "series": series}


@router.get("/mcp/stats")
def get_stats(request: Request, window: str = "24h") -> dict:
    store = _ctx(request).store
    if window not in ("24h", "7d"):
        raise ApiError(422, "validation_error", detail="window must be 24h or 7d")
    now = time.time()
    span, bucket, n = (DAY, 3600.0, 24) if window == "24h" else (7 * DAY, 6 * 3600.0, 28)
    since = now - span
    return {"window": window, "since": since, **stats(store, since, bucket, n)}
