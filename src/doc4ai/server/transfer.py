"""Transfer over slow, high-latency links (Tailscale DERP relays run at ~1 s RTT and a few Mbit/s).

* `CompressionMiddleware` gzips text responses (markdown, JSON, JS/CSS/HTML/SVG, worker modules, wasm) as they
  stream. It never touches event streams (SSE would be held back in the compressor), the future /mcp mount
  (Streamable HTTP), binary types, byte ranges (206), HEAD or anything already encoded. A compressed response's
  strong ETag becomes weak (`W/"..."`): another representation of the same content, still revalidated with
  If-None-Match (weak comparison, RFC 9110 §13.1.2), so the no-cache + ETag revalidation keeps working.
* Cache policy for the built Web UI: content-hashed / version-named files are immutable for a year; index.html is
  `no-cache` (revalidated with its ETag on every load, so a new build is picked up at once); the few other unhashed
  files (theme-init.js, favicon.svg) are stale-while-revalidate.

Brotli would be ~15% smaller again but needs a native dependency; gzip is in the standard library.
"""
from __future__ import annotations

import hashlib
import re
import zlib
from pathlib import Path

MIN_SIZE = 1024
GZIP_LEVEL = 6
# never compressed: SSE (each event must reach the browser when it is sent) and the MCP Streamable HTTP mount
EXCLUDED_PATHS = ("/api/events", "/mcp")
COMPRESSIBLE = ("text/", "application/json", "application/javascript", "application/xml", "image/svg+xml",
                "application/manifest+json", "application/wasm", "application/vnd.aidoc.pdf-ranges")

IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
# used from cache at once, revalidated in the background: the next load has the new build (theme-init.js blocks
# rendering, so revalidating it first would cost a whole round trip, measured: ~1 s at DERP latency)
BACKGROUND = "public, max-age=0, stale-while-revalidate=604800"
_VERSIONED = re.compile(r"^(assets/|pdfjs/\d+\.\d+\.\d+[^/]*/)")


def dist_cache_control(rel: str) -> str:
    """Vite names everything under assets/ by content hash; pdfjs/<version>/ is named by the pdfjs-dist version.
    HTML must be fresh (it names the hashed files of the current build, the old ones are gone after a rebuild)."""
    if _VERSIONED.match(rel):
        return IMMUTABLE
    return REVALIDATE if rel.endswith(".html") else BACKGROUND


def file_etag(path: Path) -> str:
    st = path.stat()
    return '"' + hashlib.sha256(f"{st.st_mtime_ns}-{st.st_size}".encode()).hexdigest()[:32] + '"'


def _opaque(tag: str) -> str:
    tag = tag.strip()
    return tag.removeprefix("W/")


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """If-None-Match uses the weak comparison: `W/"x"` (our gzipped representation) matches `"x"`."""
    if not if_none_match:
        return False
    mine = _opaque(etag)
    return any(t.strip() == "*" or _opaque(t) == mine for t in if_none_match.split(","))


def _accepts_gzip(value: str) -> bool:
    for part in value.split(","):
        name, _, params = part.strip().partition(";")
        if name.strip().lower() in ("gzip", "*"):
            q = re.search(r"q\s*=\s*([0-9.]+)", params)
            return not q or float(q.group(1)) > 0
    return False


def _excluded(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in EXCLUDED_PATHS)


class CompressionMiddleware:
    """Pure ASGI (no BaseHTTPMiddleware): bodies are compressed chunk by chunk as they are sent, never buffered."""

    def __init__(self, app, minimum_size: int = MIN_SIZE, level: int = GZIP_LEVEL):
        self.app = app
        self.minimum_size = minimum_size
        self.level = level

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or _excluded(scope.get("path", "")):
            return await self.app(scope, receive, send)
        req = {k.lower(): v for k, v in scope.get("headers") or []}
        gzip_ok = scope.get("method") != "HEAD" and _accepts_gzip(req.get(b"accept-encoding", b"").decode("latin-1"))
        start: dict | None = None
        compressor = None
        passthrough = True

        async def wrapped(message):
            nonlocal start, compressor, passthrough
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                h = {k.lower(): v for k, v in headers}
                ctype = h.get(b"content-type", b"").decode("latin-1").lower()
                textual = (ctype.startswith(COMPRESSIBLE) and not ctype.startswith("text/event-stream")
                           and message["status"] == 200 and b"content-encoding" not in h)
                if textual:
                    headers = _add_vary(headers)
                declared = h.get(b"content-length")
                if not (textual and gzip_ok) or (declared is not None and int(declared) < self.minimum_size):
                    return await send({**message, "headers": headers})
                passthrough = False
                start = {**message, "headers": headers}
                return None
            if message["type"] != "http.response.body" or passthrough:
                return await send(message)
            body = message.get("body", b"")
            more = message.get("more_body", False)
            if compressor is None:
                if not more and len(body) < self.minimum_size:     # small after all: send as it is
                    passthrough = True
                    await send(start)
                    return await send(message)
                compressor = zlib.compressobj(self.level, zlib.DEFLATED, 31)     # 31: gzip container
                headers = [(k, v) for k, v in start["headers"] if k.lower() not in (b"content-length", b"etag")]
                etag = next((v for k, v in start["headers"] if k.lower() == b"etag"), None)
                if etag is not None:
                    headers.append((b"etag", etag if etag.startswith(b"W/") else b"W/" + etag))
                headers.append((b"content-encoding", b"gzip"))
                if not more:
                    data = compressor.compress(body) + compressor.flush()
                    headers.append((b"content-length", str(len(data)).encode()))
                    await send({**start, "headers": headers})
                    return await send({"type": "http.response.body", "body": data})
                await send({**start, "headers": headers})
            data = compressor.compress(body)
            if not more:
                data += compressor.flush()
            if data or not more:
                await send({"type": "http.response.body", "body": data, "more_body": more})
            return None

        return await self.app(scope, receive, wrapped)


def _add_vary(headers: list) -> list:
    for i, (k, v) in enumerate(headers):
        if k.lower() == b"vary":
            if b"accept-encoding" in v.lower():
                return headers
            headers = list(headers)
            headers[i] = (k, v + b", Accept-Encoding")
            return headers
    return [*headers, (b"vary", b"Accept-Encoding")]
