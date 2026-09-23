"""Token and loopback checks (spec §1, §6 security)."""
from __future__ import annotations

import hmac

from fastapi import Request

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class ApiError(Exception):
    """Raised by endpoints; rendered as `{error, **extra}` (plus `workspace`) by the app's handler."""

    def __init__(self, status: int, error: str, **extra):
        super().__init__(error)
        self.status, self.error, self.extra = status, error, extra


def is_loopback(host: str | None) -> bool:
    return bool(host) and (host in LOOPBACK or host.startswith("127."))


def _presented_token(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return request.query_params.get("token")


def token_ok(request: Request, ctx) -> bool:
    """True when no token is configured, or the request presents it (Bearer header or ?token=)."""
    if not ctx.token:
        return True
    given = _presented_token(request)
    return given is not None and hmac.compare_digest(given.encode(), ctx.token.encode())


def require_token(request: Request) -> None:
    if not token_ok(request, request.app.state.ctx):
        raise ApiError(401, "unauthorized")


def allow_local_paths(request: Request, ctx) -> bool:
    """Local paths in requests: loopback clients, or any client presenting the configured token."""
    host = request.client.host if request.client else None
    if is_loopback(host):
        return True
    return bool(ctx.token) and token_ok(request, ctx)
