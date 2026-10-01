"""ASGI layer in front of the SDK's Streamable HTTP app (MCP spec §4.3, index D1/D5): Bearer PAT only, 401 without
`resource_metadata` (Phase 1), per-token rate limit on tools/call, 404 while disabled, and a log row for every
request the SDK middleware did not record itself."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qs

from aidoc.mcp import tokens as T
from aidoc.mcp.principal import AuthFailure, PatVerifier, Principal, parse_bearer

MIB = 1024 * 1024
_FAILURE_TEXT = {
    "missing_token": "Authentication required: send Authorization: Bearer <token>",
    "bad_format": "The token is not a Doc4AI Studio token (doc4ai_pat_...)",
    "bad_checksum": "The token is damaged (checksum mismatch); copy it again",
    "unknown_token": "Unknown token",
    "revoked": "This token was revoked",
    "expired": "This token has expired",
}


@dataclass
class CallState:
    principal: Principal | None
    ip: str | None
    user_agent: str | None
    method: str | None
    ts: float
    started: float
    protocol_version: str | None
    logged: bool = False


def max_body_bytes(cfg) -> int:
    return cfg.mcp.max_upload_mb * MIB * 4 // 3 + MIB


def www_authenticate(error: str, description: str | None, resource_metadata: str | None = None) -> str:
    parts = ['realm="doc4ai"', f'error="{error}"']
    if description:
        parts.append(f'error_description="{description}"')
    if resource_metadata:                           # Phase 2 (RFC 9728); always None in Phase 1
        parts.append(f'resource_metadata="{resource_metadata}"')
    return "Bearer " + ", ".join(parts)


def same_origin(origin: str, host: str | None) -> bool:
    """`Origin: http://h:p` names the page's own server: true when it equals the request's Host (port included)."""
    if not host:
        return False
    return origin.strip().lower().split("://", 1)[-1].rstrip("/") == host.strip().lower()


def _header(scope, name: str) -> str | None:
    want = name.lower().encode()
    for k, v in scope.get("headers") or []:
        if k.lower() == want:
            return v.decode("latin-1")
    return None


async def _send_json(send, status: int, body: dict, headers: list[tuple[bytes, bytes]] | None = None) -> None:
    raw = json.dumps(body).encode("utf-8")
    hdrs = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode()),
            (b"cache-control", b"no-store"), *(headers or [])]
    await send({"type": "http.response.start", "status": status, "headers": hdrs})
    await send({"type": "http.response.body", "body": raw})


class McpGate:
    def __init__(self, ctx, inner, verifier: PatVerifier, limiter, recorder):
        self.ctx, self.inner, self.verifier, self.limiter, self.recorder = ctx, inner, verifier, limiter, recorder

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        cfg = self.ctx.config
        if not cfg.mcp.enabled:
            return await _send_json(send, 404, {"error": "mcp_disabled"})
        ts, started = time.time(), time.perf_counter()
        ip = scope["client"][0] if scope.get("client") else None
        ua = _header(scope, "user-agent")
        pv = _header(scope, "mcp-protocol-version")
        qs = {k.lower(): v for k, v in parse_qs(scope.get("query_string", b"").decode("latin-1")).items()}
        in_url = qs.get("token") or qs.get("access_token")
        if in_url is not None:                      # spec: tokens never travel in the URL (RFC 6750 form included)
            self.recorder.record(status="auth_error", error_code="token_in_query", http_status=400, ip=ip,
                                 token_prefix_seen=T.prefix_seen(in_url[0] if in_url else None), protocol_version=pv, ts=ts)
            return await _send_json(send, 400, {"error": "token_in_query",
                                                "error_description": "Send the token in the Authorization header"})
        result = self.verifier.verify(parse_bearer(_header(scope, "authorization")))
        if isinstance(result, AuthFailure):
            text = _FAILURE_TEXT[result.reason]
            self.recorder.record(status="auth_error", error_code=result.reason, http_status=401, ip=ip,
                                 token_prefix_seen=result.prefix_seen, protocol_version=pv, ts=ts)
            return await _send_json(send, 401, {"error": "invalid_token", "error_description": text},
                                    [(b"www-authenticate", www_authenticate("invalid_token", text).encode())])
        principal = result
        origin = _header(scope, "origin")
        if origin is not None and not same_origin(origin, _header(scope, "host")):
            # spec §4.2: browsers may only call /mcp from the web UI's own origin (whatever port it was served on)
            self.recorder.record(status="protocol_error", error_code="http_403", http_status=403, ip=ip,
                                 token_id=principal.token_id, protocol_version=pv, ts=ts)
            return await _send_json(send, 403, {"error": "forbidden_origin",
                                                "error_description": "Cross-origin requests are not allowed"})
        if scope["method"] == "GET":                   # spike S12: stateless mode never sends on the standalone stream
            self.recorder.record(status="protocol_error", error_code="http_405", http_status=405, ip=ip,
                                 token_id=principal.token_id, protocol_version=pv, ts=ts)
            return await _send_json(send, 405, {"error": "method_not_allowed",
                                                "error_description": "This server is stateless: POST JSON-RPC to /mcp"},
                                    [(b"allow", b"POST")])
        # read the body once (bounded), learn the method, replay it to the SDK (D5)
        parts, size, limit = [], 0, max_body_bytes(cfg)       # a list + one join: linear, not body += chunk
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            chunk = msg.get("body", b"")
            parts.append(chunk)
            size += len(chunk)
            if size > limit:
                self.recorder.record(status="protocol_error", error_code="payload_too_large", http_status=413, ip=ip,
                                     token_id=principal.token_id, protocol_version=pv, ts=ts)
                return await _send_json(send, 413, {"error": "payload_too_large", "max_bytes": limit})
            if not msg.get("more_body"):
                break
        body = b"".join(parts)
        del parts
        # the body decides the method (D5); a client-supplied Mcp-Method header that disagrees is refused, so it
        # can never relabel a tools/call to dodge the rate limit or the audit row (the SDK checks the header
        # against the body only in the 2026-07-28 era)
        header_method = _header(scope, "mcp-method")
        body_method = None
        if scope["method"] == "POST" and body:
            try:
                parsed = json.loads(body)
                body_method = parsed.get("method") if isinstance(parsed, dict) else None
            except ValueError:
                body_method = None
            if not isinstance(body_method, str):
                body_method = None
        if header_method is not None and body_method is not None and header_method != body_method:
            self.recorder.record(status="protocol_error", error_code="method_mismatch", http_status=400, ip=ip,
                                 token_id=principal.token_id, method=body_method, protocol_version=pv, ts=ts)
            return await _send_json(send, 400, {"error": "method_mismatch",
                                                "error_description": "Mcp-Method does not match the JSON-RPC method"})
        method = body_method or header_method
        if method == "tools/call":
            ok, retry = self.limiter.acquire(principal.token_id or principal.subject, principal.rate_limit_per_min)
            if not ok:
                self.recorder.record(status="rate_limited", error_code="rate_limited", http_status=429, ip=ip,
                                     token_id=principal.token_id, method=method, protocol_version=pv, ts=ts)
                return await _send_json(send, 429, {"error": "rate_limited", "retry_after": retry,
                                                    "error_description": f"Retry after {retry} s"},
                                        [(b"retry-after", str(retry).encode())])
        state = CallState(principal=principal, ip=ip, user_agent=ua, method=method, ts=ts, started=started,
                          protocol_version=pv)
        scope.setdefault("state", {})["doc4ai"] = state

        replayed = {"done": False}

        async def replay():
            if not replayed["done"]:
                replayed["done"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        status_box = {"status": None, "bytes": 0}

        async def wrapped_send(message):
            if message["type"] == "http.response.start":
                status_box["status"] = message["status"]
            elif message["type"] == "http.response.body":
                status_box["bytes"] += len(message.get("body", b""))
            await send(message)

        try:
            await self.inner(scope, replay, wrapped_send)
        finally:
            if not state.logged:                       # the SDK middleware never saw it (421/400/405/202 ...)
                http = status_box["status"] or 500
                self.recorder.record(status="ok" if 200 <= http < 300 else "protocol_error",
                                     error_code=None if 200 <= http < 300 else f"http_{http}", http_status=http,
                                     token_id=principal.token_id, method=method, ip=ip, protocol_version=pv, ts=ts,
                                     duration_ms=int((time.perf_counter() - started) * 1000),
                                     response_bytes=status_box["bytes"])
                state.logged = True
                self.recorder.touch_token(principal.token_id, ip, ua)    # the middleware touches with the client label
