"""PAT authentication and call logging for the docling-serve compatible API (compat spec §5, §6; plan D1, D2).

`/v1` routes are ordinary FastAPI routes: `Depends(compat_principal)` returns the `Principal` or raises a
`CompatError` rendered docling/FastAPI style (`{"detail": ...}`). The same verifier, auth-flood guard and call
recorder as `/mcp` are used (`ctx.extras["mcp"]`). Every request writes exactly one `mcp_calls` row
(`method = "compat/docling"`): auth failures from the dependency itself, everything else from `CompatLogMiddleware`
once the response is sent."""
from __future__ import annotations

import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from aidoc.mcp import tokens as T
from aidoc.mcp.gate import _FAILURE_TEXT as _MCP_FAILURE_TEXT
from aidoc.mcp.gate import same_origin, www_authenticate
from aidoc.mcp.principal import SCOPE_CONVERT, SCOPE_READ, AuthFailure, Principal, parse_bearer

METHOD = "compat/docling"
PROTOCOL_V1 = "docling-serve-v1"
PROTOCOL_V1ALPHA = "docling-serve-v1alpha"
_FAILURE_TEXT = {**_MCP_FAILURE_TEXT,
                 "missing_token": "Authentication required: send X-Api-Key: <token> or Authorization: Bearer <token>"}
_QUERY_TOKEN_KEYS = {"api_key", "token", "access_token"}


class CompatError(HTTPException):
    """Rendered as `{"detail": <detail>, **extra}` with the given status and headers (app.py handler)."""

    def __init__(self, status_code: int, detail, headers: dict | None = None, **extra):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.extra = extra


@dataclass
class CompatCall:
    """request.state.compat: what the single log row of this request will say."""
    principal: Principal | None
    ts: float
    started: float
    protocol: str
    ip: str | None
    ua: str | None
    tool_name: str | None = None
    job_id: str | None = None
    args: dict | None = None
    status: str = "ok"
    error_code: str | None = None
    response_tokens_est: int | None = None
    logged: bool = False

    def fail(self, error_code: str, status: str = "tool_error") -> None:
        self.status, self.error_code = status, error_code


def protocol_for(path: str) -> str:
    return PROTOCOL_V1ALPHA if path.startswith("/v1alpha/") else PROTOCOL_V1


def ua_identity(ua: str | None) -> tuple[str, str | None]:
    """(name, version) of the first product token of a User-Agent: `python-requests/2.32.3` -> ("python-requests",
    "2.32.3"); empty -> ("unknown", None)."""
    from aidoc.mcp.calllog import ua_identity as _ua
    return _ua(ua)


def _www(text: str) -> dict:
    return {"WWW-Authenticate": www_authenticate("invalid_token", text)}     # no resource_metadata until Spec 4 (4b)


def new_call(request: Request, principal: Principal | None = None) -> CompatCall:
    call = CompatCall(principal=principal, ts=time.time(), started=time.perf_counter(),
                      protocol=protocol_for(request.url.path),
                      ip=request.client.host if request.client else None, ua=request.headers.get("user-agent"))
    request.state.compat = call
    return call


def require_enabled(request: Request) -> None:
    if not request.app.state.ctx.config.compat.enabled:
        raise CompatError(404, "compat API disabled")


def _fail(ctx, rt, call: CompatCall, reason: str, status: int, text: str, *, prefix: str | None,
          token_id: str | None = None):
    retry = rt.guard.on_failure(call.ip, reason, prefix, call.protocol)
    call.logged = True                                  # the guard's aggregate row or the row below
    if retry is not None:
        raise CompatError(429, f"{text}. Too many failed authentications from this address; retry after {retry} s",
                          headers={"Retry-After": str(retry), **_www(text)}, reason=reason, retry_after=retry)
    rt.recorder.record(status="auth_error", error_code=reason, http_status=status, ip=call.ip, token_id=token_id,
                       token_prefix_seen=prefix, protocol_version=call.protocol, method=METHOD, ts=call.ts)
    raise CompatError(status, text, headers=_www(text) if status == 401 else None)


def _principal(request: Request, scopes_any: tuple[str, ...]) -> Principal:
    require_enabled(request)
    ctx = request.app.state.ctx
    rt = ctx.extras["mcp"]
    call = new_call(request)
    in_query = [v for k, v in request.query_params.multi_items() if k.lower() in _QUERY_TOKEN_KEYS]
    if in_query:                                        # tokens never travel in the URL (MCP spec §4.2)
        _fail(ctx, rt, call, "token_in_query", 400, "send the token in X-Api-Key or Authorization",
              prefix=T.prefix_seen(in_query[0]))
    key = (request.headers.get("x-api-key") or "").strip().strip('"').strip() or None
    bearer = parse_bearer(request.headers.get("authorization"))
    if key and bearer and key != bearer:
        _fail(ctx, rt, call, "bad_format", 401, "X-Api-Key and Authorization disagree", prefix=T.prefix_seen(key))
    result = rt.verifier.verify(key or bearer)
    if isinstance(result, AuthFailure):
        _fail(ctx, rt, call, result.reason, 401, _FAILURE_TEXT[result.reason], prefix=result.prefix_seen,
              token_id=result.token_id)
    origin = request.headers.get("origin")
    if origin is not None and not same_origin(origin, request.headers.get("host")):
        call.principal = result
        call.fail("http_403", "protocol_error")
        raise CompatError(403, "cross-origin request")
    call.principal = result
    if not any(result.has(s) for s in scopes_any):
        call.fail("forbidden_scope", "forbidden_scope")
        raise CompatError(403, f"token lacks {scopes_any[0]}")
    return result


def compat_principal(request: Request) -> Principal:
    """Convert endpoints: doc4ai:convert."""
    return _principal(request, (SCOPE_CONVERT,))


def compat_principal_read(request: Request) -> Principal:
    """Poll / result: doc4ai:convert or doc4ai:read (only the creating token sees its tasks)."""
    return _principal(request, (SCOPE_CONVERT, SCOPE_READ))


def compat_principal_assets(request: Request) -> Principal:
    return _principal(request, (SCOPE_READ,))


def finish_call(ctx, call: CompatCall, http_status: int, response_bytes: int) -> None:
    """The one mcp_calls row of a request that got past (or was refused after) authentication."""
    if call.logged:
        return
    call.logged = True
    rec = ctx.extras["mcp"].recorder
    token_id = call.principal.token_id if call.principal else None
    client_id = None
    if token_id:
        name, version = ua_identity(call.ua)
        client_id = rec.note_client(token_id=token_id, client_name=name, client_version=version,
                                    protocol_version=call.protocol, user_agent=call.ua, ip=call.ip)
    if call.status == "ok" and http_status >= 400:
        call.status, call.error_code = "protocol_error", call.error_code or f"http_{http_status}"
    rec.record(status=call.status, error_code=call.error_code, http_status=http_status, method=METHOD,
               tool_name=call.tool_name, token_id=token_id, client_id=client_id, ip=call.ip,
               protocol_version=call.protocol, job_id=call.job_id, args=call.args,
               duration_ms=int((time.perf_counter() - call.started) * 1000), response_bytes=response_bytes,
               response_tokens_est=call.response_tokens_est, ts=call.ts)
    if token_id:
        name, version = ua_identity(call.ua)
        rec.touch_token(token_id, call.ip, f"{name}/{version}" if version else name)


class CompatLogMiddleware:
    """Pure ASGI: after a /v1 (or /v1alpha) response is sent, writes the request's row from request.state.compat
    with the real status and byte count. Requests refused before a CompatCall existed are not logged here."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith(("/v1/", "/v1alpha/")):
            return await self.app(scope, receive, send)
        box = {"status": 500, "bytes": 0}

        async def wrapped(message):
            if message["type"] == "http.response.start":
                box["status"] = message["status"]
            elif message["type"] == "http.response.body":
                box["bytes"] += len(message.get("body", b""))
            await send(message)
        try:
            await self.app(scope, receive, wrapped)
        finally:
            call = (scope.get("state") or {}).get("compat")
            if call is not None and not call.logged:
                ctx = scope["app"].state.ctx
                finish_call(ctx, call, box["status"], box["bytes"])
