"""Discovery documents (spec 2026-11-05 §4): the issuer and the canonical resource are derived from the Host header
of the request — only when that Host is one this server answers to (`aidoc.mcp.server._hosts_for`, the same list the
SDK's DNS-rebinding guard uses). An unknown Host never becomes an issuer string."""
from __future__ import annotations

import re
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from aidoc.mcp.principal import SCOPES

DEFAULT_CHALLENGE_SCOPE = "doc4ai:read doc4ai:convert"       # the 401's suggested scope (spec §4.5)
METADATA_HEADERS = {"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"}
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_PORT_RE = re.compile(r"^\d{1,5}$")
_HOSTS_TTL_S = 30.0
_hosts_cache: dict[tuple, tuple[float, frozenset]] = {}
_hosts_lock = threading.Lock()


class UnknownHost(Exception):
    """The request's Host is not one this server answers to."""


def _known_hosts(ctx) -> frozenset:
    from aidoc.mcp.server import _hosts_for
    cfg = ctx.config
    bind_host = str(ctx.extras.get("bind_host") or cfg.server.host)
    key = (bind_host, tuple(cfg.mcp.allowed_hosts))
    now = time.monotonic()
    with _hosts_lock:
        hit = _hosts_cache.get(key)
        if hit is not None and now - hit[0] < _HOSTS_TTL_S:
            return hit[1]
    hosts = frozenset(h.lower() for h in _hosts_for(cfg, bind_host))
    with _hosts_lock:
        _hosts_cache[key] = (now, hosts)
    return hosts


def bound_port(ctx) -> int:
    return int(ctx.extras.get("bind_port") or ctx.config.server.port)


def allowed_host(ctx, host_header: str | None) -> str | None:
    """The lower-cased `host[:port]` when its host part is one this server answers to and its port is the one it is
    bound to (or absent, when that port is 80, the http default); else None (verifier F5)."""
    if not host_header:
        return None
    h = host_header.strip().lower()
    if h.startswith("["):
        end = h.find("]")
        if end < 0:
            return None
        name, rest = h[: end + 1], h[end + 1:]
        if rest and not (rest.startswith(":") and _PORT_RE.match(rest[1:])):
            return None
        port = rest[1:]
    else:
        if h.count(":") > 1:
            return None
        name, _, port = h.partition(":")
        if ":" in h and not _PORT_RE.match(port):
            return None
    if not name or name not in _known_hosts(ctx):
        return None
    bound = bound_port(ctx)
    if (port and (port != str(int(port)) or int(port) != bound)) or (not port and bound != 80):
        return None
    return f"{name}:{port}" if port else name


def _host_ok(ctx, request: Request) -> bool:
    from aidoc.server.auth import host_ok
    return host_ok(request, ctx)


def issuer(ctx, request: Request) -> str:
    """`http://<host[:port]>` of this request (scheme fixed: local plain HTTP; a future public_url replaces this)."""
    if not _host_ok(ctx, request):
        raise UnknownHost()
    h = allowed_host(ctx, request.headers.get("host"))
    if h is None:
        raise UnknownHost()
    return "http://" + h


def issuer_or_none(ctx, request: Request) -> str | None:
    try:
        return issuer(ctx, request)
    except UnknownHost:
        return None


def canonical(ctx, request: Request) -> str:
    return issuer(ctx, request) + "/mcp"


def prm_url(iss: str) -> str:
    return iss + "/.well-known/oauth-protected-resource/mcp"


def prm_doc(iss: str) -> dict:
    return {"resource": iss + "/mcp", "authorization_servers": [iss], "scopes_supported": list(SCOPES),
            "bearer_methods_supported": ["header"], "resource_name": "Doc4AI Studio",
            "resource_documentation": iss + "/mcp-admin"}


def as_metadata(cfg, iss: str) -> dict:
    o = cfg.oauth
    m = {"issuer": iss, "authorization_endpoint": iss + "/oauth/authorize", "token_endpoint": iss + "/oauth/token"}
    if o.allow_dcr:
        m["registration_endpoint"] = iss + "/oauth/register"
    m.update({"revocation_endpoint": iss + "/oauth/revoke", "response_types_supported": ["code"],
              "response_modes_supported": ["query"], "grant_types_supported": ["authorization_code", "refresh_token"],
              "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"],
              "revocation_endpoint_auth_methods_supported": ["none"], "scopes_supported": list(SCOPES)})
    if o.allow_cimd:
        m["client_id_metadata_document_supported"] = True
    m.update({"authorization_response_iss_parameter_supported": True, "resource_indicators_supported": True,
              "service_documentation": iss + "/mcp-admin"})
    return m


def not_found() -> JSONResponse:
    return JSONResponse({"error": "not_found"}, status_code=404, headers=NO_STORE)


def unknown_host() -> JSONResponse:
    return JSONResponse({"error": "invalid_request", "error_description": "unknown host"}, status_code=400, headers=NO_STORE)


router = APIRouter()


def _doc(request: Request, build) -> JSONResponse:
    ctx = request.app.state.ctx
    if not ctx.config.oauth.enabled:
        return not_found()
    iss = issuer_or_none(ctx, request)
    if iss is None:
        return unknown_host()
    return JSONResponse(build(ctx, iss), headers=METADATA_HEADERS)


@router.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
@router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
def protected_resource(request: Request):
    return _doc(request, lambda ctx, iss: prm_doc(iss))


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
@router.get("/.well-known/openid-configuration", include_in_schema=False)
def authorization_server(request: Request):
    return _doc(request, lambda ctx, iss: as_metadata(ctx.config, iss))
