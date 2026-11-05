"""`/oauth/*` (spec 2026-11-05 §4.1, §5): register (DCR), token, revoke, authorize. Every route answers 404 while
`oauth.enabled` is false and 400 (never an issuer) for a Host this server does not answer to."""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from aidoc.oauth.clients import ClientError, ClientRegistry
from aidoc.oauth.metadata import DEFAULT_CHALLENGE_SCOPE, NO_STORE, issuer_or_none, not_found, unknown_host

__all__ = ["AUTHORIZE_GET_PER_MIN", "DEFAULT_CHALLENGE_SCOPE", "OAuthRuntime", "router"]

AUTHORIZE_GET_PER_MIN = 60
REGISTER_MAX_BYTES = 64 * 1024

router = APIRouter()


class OAuthRuntime:
    """Per-app OAuth state (app.state.oauth): the client registry with its fetch / registration counters."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.registry = ClientRegistry(ctx)


def runtime(request: Request) -> OAuthRuntime:
    return request.app.state.oauth


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def oauth_json(body: dict, status: int = 200, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={**NO_STORE, **(headers or {})})


def _gate(request: Request):
    """(issuer, None) or (None, response) for a disabled server or an unknown Host."""
    ctx = request.app.state.ctx
    if not ctx.config.oauth.enabled:
        return None, not_found()
    iss = issuer_or_none(ctx, request)
    if iss is None:
        return None, unknown_host()
    return iss, None


@router.post("/oauth/register", include_in_schema=False)
async def register(request: Request):
    ctx = request.app.state.ctx
    if ctx.config.oauth.enabled and not ctx.config.oauth.allow_dcr:
        return not_found()
    _iss, refused = _gate(request)
    if refused is not None:
        return refused
    raw = await request.body()
    try:
        body = json.loads(raw) if len(raw) <= REGISTER_MAX_BYTES else None
    except (ValueError, RecursionError):
        body = None
    try:
        out = runtime(request).registry.register_dcr(body, _ip(request), time.time())
    except ClientError as e:
        if e.code == "rate_limited":
            return oauth_json({"error": "rate_limited", "error_description": e.message}, 429,
                              {"Retry-After": str(e.retry_after or 60)})
        return oauth_json({"error": e.code, "error_description": e.message}, 400)
    return oauth_json(out, 201)


# ---- token and revocation (spec §5.3 (4)–(5))
_FORM = "application/x-www-form-urlencoded"
TOKEN_HEADERS = {"Pragma": "no-cache"}


async def _form(request: Request) -> dict:
    """The urlencoded body as {key: value}; a repeated key or another content type is invalid_request (RFC 6749 §3.2)."""
    from urllib.parse import parse_qs

    from aidoc.oauth.grants import OAuthError
    ctype = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    raw = await request.body()
    if not raw:
        return {}                                       # no parameters at all: each endpoint says what is missing
    if ctype != _FORM:
        raise OAuthError("invalid_request", "the body must be application/x-www-form-urlencoded")
    if len(raw) > REGISTER_MAX_BYTES:
        raise OAuthError("invalid_request", "the body is too large")
    try:
        parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True, strict_parsing=False)
    except UnicodeDecodeError:
        raise OAuthError("invalid_request", "the body is not UTF-8") from None
    if any(len(v) > 1 for v in parsed.values()):
        raise OAuthError("invalid_request", "a parameter was sent more than once")
    return {k: v[0] for k, v in parsed.items()}


@router.post("/oauth/token", include_in_schema=False)
async def token(request: Request):
    from aidoc.oauth import tokens as T
    from aidoc.oauth.grants import GrantService, OAuthError
    iss, refused = _gate(request)
    if refused is not None:
        return refused
    ctx = request.app.state.ctx
    ip, form = _ip(request), {}
    try:
        form = await _form(request)
        gt = form.get("grant_type")
        svc = GrantService(ctx)
        if gt == "authorization_code":
            tr = svc.exchange_code(form, ip, time.time(), resource=iss + "/mcp")
        elif gt == "refresh_token":
            tr = svc.refresh(form, ip, time.time(), resource=iss + "/mcp")
        elif not gt:
            raise OAuthError("invalid_request", "missing grant_type")
        else:
            raise OAuthError("unsupported_grant_type", "only authorization_code and refresh_token are supported")
    except OAuthError as e:
        seen = T.prefix_seen(form.get("code") or form.get("refresh_token"))
        retry = ctx.extras["mcp"].guard.on_failure(ip, f"oauth_{e.error}", seen)
        extra = {"Retry-After": str(retry)} if retry is not None else {}
        return oauth_json({"error": e.error, "error_description": e.description}, 429 if retry is not None else e.status,
                          {**TOKEN_HEADERS, **extra})
    return oauth_json(tr.body(), 200, TOKEN_HEADERS)


@router.post("/oauth/revoke", include_in_schema=False)
async def revoke(request: Request):
    """RFC 7009: 200 {} whether or not the token existed; a known AT or RT revokes its whole grant."""
    from aidoc.oauth.grants import GrantService, OAuthError
    _iss, refused = _gate(request)
    if refused is not None:
        return refused
    try:
        form = await _form(request)
    except OAuthError as e:
        return oauth_json({"error": e.error, "error_description": e.description}, 400)
    GrantService(request.app.state.ctx).revoke_by_token(form.get("token"), time.time())
    return oauth_json({}, 200)
