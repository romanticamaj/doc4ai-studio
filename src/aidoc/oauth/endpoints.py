"""`/oauth/*` (spec 2026-11-05 §4.1, §5): register (DCR), token, revoke, authorize. Every route answers 404 while
`oauth.enabled` is false and 400 (never an issuer) for a Host this server does not answer to."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from aidoc.oauth.clients import DCR_METADATA_MAX, ClientError, ClientRegistry
from aidoc.oauth.metadata import DEFAULT_CHALLENGE_SCOPE, NO_STORE, issuer_or_none, not_found, unknown_host

__all__ = ["AUTHORIZE_GET_PER_MIN", "DEFAULT_CHALLENGE_SCOPE", "OAuthRuntime", "router"]

AUTHORIZE_GET_PER_MIN = 60
OAUTH_WORKERS = 4                    # the executor client resolution runs on (> CIMD_MAX_CONCURRENT: lookups never wait)
REGISTER_MAX_BYTES = 64 * 1024

router = APIRouter()


class OAuthRuntime:
    """Per-app OAuth state (app.state.oauth): client registry, its resolution executor, authorize limiter, used nonces."""

    def __init__(self, ctx):
        from aidoc.mcp.ratelimit import RateLimiter
        from aidoc.oauth.consent import NonceSet
        self.ctx = ctx
        self.registry = ClientRegistry(ctx)
        self.authorize_limiter = RateLimiter(lambda: AUTHORIZE_GET_PER_MIN)
        # client resolution can fetch a CIMD document (up to CIMD_TIMEOUT_S): it runs here, never on the shared request
        # threadpool or the event loop, so slow client documents cannot starve other requests (verifier F1)
        self.executor = ThreadPoolExecutor(max_workers=OAUTH_WORKERS, thread_name_prefix="aidoc-oauth")
        self.nonces = NonceSet(ttl_s=1800)                 # the longest consent_ttl_s: a nonce outlives its request

    async def resolve(self, client_id: str, now: float) -> dict:
        import asyncio
        return await asyncio.get_running_loop().run_in_executor(self.executor, self.registry.resolve, client_id, now)


def runtime(request: Request) -> OAuthRuntime:
    return request.app.state.oauth


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def oauth_json(body: dict, status: int = 200, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={**NO_STORE, **(headers or {})})


def _cross_site(request: Request, iss: str) -> bool:
    """A browser request from another site: an Origin other than this issuer (`null` included), or Sec-Fetch-Site
    other than same-origin / none. Requests without either header come from programs, not web pages."""
    origin, site = request.headers.get("origin"), request.headers.get("sec-fetch-site")
    return (origin is not None and origin.strip().lower().rstrip("/") != iss) or \
        (site is not None and site.strip().lower() not in ("same-origin", "none"))


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
    iss, refused = _gate(request)
    if refused is not None:
        return refused
    # verifier F2: a web page must not register clients (and use up the quota) with a no-cors text/plain POST
    if _cross_site(request, iss):
        return oauth_json({"error": "cross_origin", "error_description": "client registration is not accepted cross-site"}, 403)
    ctype = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if ctype != "application/json":
        return oauth_json({"error": "invalid_client_metadata", "error_description": "the body must be application/json"}, 400)
    raw = await request.body()
    if len(raw) > DCR_METADATA_MAX:
        return oauth_json({"error": "invalid_client_metadata",
                           "error_description": f"the registration is larger than {DCR_METADATA_MAX} bytes"}, 400)
    try:
        body = json.loads(raw)
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


# ---- authorize (spec §5.3 (1)–(3), §6)
def _page(body: str, status: int, extra: dict | None = None):
    from fastapi.responses import HTMLResponse

    from aidoc.oauth.consent import CONSENT_HEADERS
    return HTMLResponse(body, status_code=status, headers={**CONSENT_HEADERS, **(extra or {})})


def _error_page(title: str, status: int = 400, reason: str | None = None, extra: dict | None = None):
    from aidoc.oauth.consent import render_error
    return _page(render_error(title, reason), status, extra)


def _with_query(uri: str, params: dict) -> str:
    from urllib.parse import urlencode
    sep = "&" if "?" in uri else "?"
    return uri + sep + urlencode(params)


def _redirect(uri: str, params: dict):
    from fastapi.responses import Response

    from aidoc.oauth.consent import CONSENT_HEADERS
    return Response(status_code=302, headers={**CONSENT_HEADERS, "Location": _with_query(uri, params)})


def _client_error_page(e: ClientError):
    from aidoc.oauth.consent import TEXT
    if e.code == "unknown_client":
        return _error_page(TEXT["err_unknown_client"])
    if e.code == "cimd_fetch_failed":
        return _error_page(TEXT["err_cimd"].format(reason=e.message))
    return _error_page(TEXT["err_client"].format(reason=e.message))


def _requested_scopes(raw: str | None) -> list[str] | None:
    """Canonical order; None when a scope is unknown. doc4ai:read is always part of the request (every connection
    needs it; spec §6.1 「至少要有 doc4ai:read」)."""
    from aidoc.mcp.principal import SCOPE_READ, SCOPES
    asked = (raw if raw is not None else DEFAULT_CHALLENGE_SCOPE).split()
    if any(s not in SCOPES for s in asked):
        return None
    want = set(asked) | {SCOPE_READ}
    return [s for s in SCOPES if s in want]


def _record(ctx, status: str, http_status: int, ip, *, error_code=None, args=None) -> None:
    ctx.extras["mcp"].recorder.record(status=status, error_code=error_code, http_status=http_status, ip=ip,
                                      method="oauth/authorize", args=args)


def _host_of(uri: str) -> str | None:
    from urllib.parse import urlsplit
    try:
        return urlsplit(uri).hostname or uri.split(":", 1)[0]
    except ValueError:
        return None


@router.get("/oauth/authorize", include_in_schema=False)
async def authorize_get(request: Request):
    from aidoc.mcp.principal import SCOPE_CONVERT, SCOPE_READ
    from aidoc.oauth.clients import classify_redirect, redirect_allowed
    from aidoc.oauth.consent import TEXT, ConsentView, render_consent, sign_request
    from aidoc.oauth.grants import CHALLENGE_RE, source_label
    ctx = request.app.state.ctx
    if not ctx.config.oauth.enabled:
        return not_found()
    iss = issuer_or_none(ctx, request)
    if iss is None:
        return _error_page(TEXT["err_bad_host"])
    rt = runtime(request)
    ok, retry = rt.authorize_limiter.acquire(f"authz:{_ip(request)}")
    if not ok:
        return _error_page(TEXT["err_too_many"], 429, extra={"Retry-After": str(retry)})
    q = request.query_params
    now = time.time()
    try:
        client = await rt.resolve(q.get("client_id") or "", now)
    except ClientError as e:
        return _client_error_page(e)
    redirect_uri = q.get("redirect_uri")
    if not redirect_uri or not redirect_allowed(client, redirect_uri):
        return _error_page(TEXT["err_bad_redirect"], reason=redirect_uri)
    state = q.get("state")
    if not state:
        return _error_page(TEXT["err_missing_state"])
    # from here on the client and its redirect URI are known. Errors go back to it (with state and iss) only when the
    # redirect URI returns to a program on this computer (loopback or a private-use scheme). An https redirect URI
    # comes from an unverified client document or registration: answering it with a 302 before any consent would make
    # this server an open redirector (RFC 9700 §4.11.2; verifier F3), so it gets the error page here instead.
    local_redirect = classify_redirect(redirect_uri) in ("loopback", "private")

    def back(error: str, description: str):
        if not local_redirect:
            return _error_page(TEXT["err_param"], reason=f"{error}: {description}")
        return _redirect(redirect_uri, {"error": error, "error_description": description, "state": state, "iss": iss})
    if q.get("response_type") != "code":
        return back("unsupported_response_type", "only response_type=code is supported")
    if q.get("code_challenge_method") != "S256":
        return back("invalid_request", "PKCE with code_challenge_method=S256 is required")
    challenge = q.get("code_challenge") or ""
    if not CHALLENGE_RE.match(challenge):
        return back("invalid_request", "code_challenge must be 43-128 characters of [A-Za-z0-9._~-]")
    requested = _requested_scopes(q.get("scope"))
    if requested is None:
        return back("invalid_scope", "unknown scope requested")
    resource = iss + "/mcp"
    if q.get("resource") is not None and q.get("resource") != resource:
        return back("invalid_target", "resource must be this server's /mcp URL")
    blob = sign_request(ctx.extras["mcp"].secret, {"client_id": client["client_id"], "redirect_uri": redirect_uri,
                                                   "scope": " ".join(requested), "state": state, "code_challenge": challenge,
                                                   "resource": resource}, exp=now + ctx.config.oauth.consent_ttl_s)
    cimd = client["client_id"].startswith("https://")
    view = ConsentView(client_name=client.get("name") or client["client_id"], source_kind="cimd" if cimd else "dcr",
                       source_label=source_label(client, client["client_id"]) if cimd else client["client_id"],
                       redirect_uri=redirect_uri, redirect_kind=classify_redirect(redirect_uri) or "https", issuer=iss,
                       requested_scopes=requested, checked_scopes=[s for s in requested if s in (SCOPE_READ, SCOPE_CONVERT)],
                       needs_token=bool(ctx.token), request_blob=blob)
    return _page(render_consent(view), 200)


async def _consent_form(request: Request) -> dict[str, list[str]] | None:
    from urllib.parse import parse_qs
    ctype = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    raw = await request.body()
    if ctype != _FORM or len(raw) > REGISTER_MAX_BYTES:
        return None
    try:
        return parse_qs(raw.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError:
        return None


@router.post("/oauth/authorize", include_in_schema=False)
async def authorize_post(request: Request):
    import hmac

    from aidoc.mcp.principal import SCOPE_READ
    from aidoc.oauth.clients import redirect_allowed
    from aidoc.oauth.consent import TEXT, ConsentError, verify_request
    from aidoc.oauth.grants import GrantService, source_label
    ctx = request.app.state.ctx
    if not ctx.config.oauth.enabled:
        return not_found()
    iss = issuer_or_none(ctx, request)
    if iss is None:
        return _error_page(TEXT["err_bad_host"])
    # CSRF (spec §6.2): a browser says where the form came from; only this page itself may submit it
    if _cross_site(request, iss):
        return oauth_json({"error": "cross_origin", "error_description": TEXT["err_cross_site"]}, 403)
    ip, now = _ip(request), time.time()
    form = await _consent_form(request)
    if form is None or len(form.get("request", [])) != 1:
        return _error_page(TEXT["err_bad_request"])
    try:
        req = verify_request(ctx.extras["mcp"].secret, form["request"][0], now)
    except ConsentError as e:
        return _error_page(e.message)
    if req.get("resource") != iss + "/mcp":
        return _error_page(TEXT["err_bad_request"])           # the page was served for another Host
    rt = runtime(request)
    if ctx.token:
        given = (form.get("server_token") or [""])[0]
        if not hmac.compare_digest(given.encode("utf-8"), ctx.token.encode("utf-8")):
            retry = ctx.extras["mcp"].guard.on_failure(ip, "consent_token", None)
            if retry is not None:
                return _error_page(TEXT["err_too_many"], 429, extra={"Retry-After": str(retry)})
            _record(ctx, "auth_error", 401, ip, error_code="consent_token")
            return _error_page(TEXT["err_wrong_token"], 401)
    decision = (form.get("decision") or [""])[0]
    if decision not in ("allow", "deny"):
        return _error_page(TEXT["err_bad_request"])
    try:
        client = await rt.resolve(req["client_id"], now)
    except ClientError as e:
        return _client_error_page(e)
    if not redirect_allowed(client, req["redirect_uri"]):
        return _error_page(TEXT["err_bad_redirect"], reason=req["redirect_uri"])
    requested = req["scope"].split()
    granted = form.get("scopes", [])
    if decision == "allow" and (SCOPE_READ not in granted or not set(granted) <= set(requested)):
        return _error_page(TEXT["err_scopes"])
    if not rt.nonces.consume(req["nonce"], now):
        return _error_page(TEXT["err_used"])
    base = {"state": req["state"], "iss": iss}
    if decision == "deny":
        _record(ctx, "ok", 302, ip, args={"client_host": source_label(client, client["client_id"]), "decision": "deny"})
        return _redirect(req["redirect_uri"], {"error": "access_denied", "error_description": "the user denied the request",
                                               **base})
    scopes = [s for s in requested if s in set(granted)]
    code = GrantService(ctx).issue_code(client, req["redirect_uri"], req["code_challenge"], scopes, req["resource"], ip, now)
    _record(ctx, "ok", 302, ip, args={"client_host": source_label(client, client["client_id"]), "scopes": scopes,
                                      "redirect_host": _host_of(req["redirect_uri"])})
    return _redirect(req["redirect_uri"], {"code": code, **base})
