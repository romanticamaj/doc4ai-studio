"""Token and loopback checks (spec §1, §6 security)."""
from __future__ import annotations

import hmac
import ipaddress

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


_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _hostname(hostport: str) -> str:
    h = hostport.strip().lower()
    if h.startswith("["):                                   # [::1]:8765
        return h[1:h.index("]")] if "]" in h else h[1:]
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def host_ok(request: Request, ctx) -> bool:
    """Without a token the server trusts loopback clients, so it must only answer requests addressed to a loopback
    name (or the address it was bound to): a DNS-rebinding page reaches 127.0.0.1 under its own Host name."""
    if ctx.token:
        return True
    host = request.headers.get("host")
    if not host:
        return True                                         # HTTP/1.0 clients; browsers always send Host
    name = _hostname(host)
    allowed = {"127.0.0.1", "localhost", "::1"}
    bind = str(ctx.extras.get("bind_host") or "").lower()
    if bind and bind not in ("0.0.0.0", "::"):
        allowed.add(bind)
    if name in allowed:
        return True
    try:
        return ipaddress.ip_address(name).is_loopback            # 127.x.y.z, never a name like "127.evil.com"
    except ValueError:
        return False


def origin_ok(request: Request) -> bool:
    """State-changing requests from a browser must be same-origin (blocks cross-site form POSTs / CSRF);
    requests without an Origin header (curl, the CLI) are not from a cross-site page."""
    if request.method in _SAFE_METHODS:
        return True
    origin = request.headers.get("origin")
    if origin is None:
        return True
    host = (request.headers.get("host") or "").lower()
    return origin.lower().split("://", 1)[-1].rstrip("/") == host


def require_token(request: Request) -> None:
    ctx = request.app.state.ctx
    if not host_ok(request, ctx):
        raise ApiError(403, "bad_host")
    if not token_ok(request, ctx):
        raise ApiError(401, "unauthorized")
    if not origin_ok(request):
        raise ApiError(403, "cross_origin")


def allow_local_paths(request: Request, ctx) -> bool:
    """Local paths in requests: loopback clients, or any client presenting the configured token."""
    host = request.client.host if request.client else None
    if is_loopback(host):
        return True
    return bool(ctx.token) and token_ok(request, ctx)
