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
