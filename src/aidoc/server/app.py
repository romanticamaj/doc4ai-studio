"""FastAPI app factory (spec §6). Every JSON body carries `"workspace": "default"`."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

from aidoc.config import AidocConfig
from aidoc.server import auth
from aidoc.server.auth import ApiError
from aidoc.server.context import ServerContext
from aidoc.store import Store

WORKSPACE = "default"
_ERROR_NAMES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
                405: "method_not_allowed"}


def envelope(data: dict) -> dict:
    return {**data, "workspace": WORKSPACE}


def error_response(http_status: int, error: str, headers: dict | None = None, **extra) -> JSONResponse:
    return JSONResponse(envelope({"error": error, **extra}), status_code=http_status, headers=headers)


class EnvelopeMiddleware:
    """Pure ASGI, so streaming responses (SSE, files) pass through untouched; JSON object bodies gain `workspace`."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        start: dict | None = None
        chunks: list[bytes] = []

        async def wrapped(message):
            nonlocal start
            if message["type"] == "http.response.start":
                ctype = dict(message.get("headers") or []).get(b"content-type", b"")
                if ctype.startswith(b"application/json"):
                    start = message
                    return None
                return await send(message)
            if message["type"] == "http.response.body" and start is not None:
                chunks.append(message.get("body", b""))
                if message.get("more_body"):
                    return None
                body = b"".join(chunks)
                try:
                    data = json.loads(body) if body else None
                except ValueError:
                    data = None
                if isinstance(data, dict) and "workspace" not in data:
                    data["workspace"] = WORKSPACE
                    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(body)).encode()))
                await send({**start, "headers": headers})
                return await send({"type": "http.response.body", "body": body})
            return await send(message)

        return await self.app(scope, receive, wrapped)


def build_context(cfg: AidocConfig, token: str | None, start_workers: bool = True) -> ServerContext:
    from aidoc.server.jobs import JobQueue
    from aidoc.server.sse import EventBus
    from aidoc.server.uploads import UploadManager
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(cfg.data_dir / "aidoc.db", threadsafe=True)
    ctx = ServerContext(config=cfg, store=store, bus=None, queue=None, uploads=None, token=token or None,
                        started_at=time.time())
    ctx.bus = EventBus(store)
    ctx.uploads = UploadManager(ctx)
    ctx.queue = JobQueue(ctx)
    if start_workers:
        ctx.queue.start()
        start_reassessment(ctx)
    return ctx


def start_reassessment(ctx: ServerContext, log=None) -> threading.Thread:
    """Background re-assessment of existing outputs after startup recovery (spec 2026-10-01 §11): never blocks
    the server; the store lock is taken per statement, not for the whole run."""
    import sys

    from aidoc.reassess import reassess_all

    def emit(line: str) -> None:
        print(f"reassess: {line}", file=sys.stderr, flush=True)

    def run() -> None:
        try:
            counts = reassess_all(ctx.store, log=log or emit)
        except Exception as e:  # noqa: BLE001  a background helper must never take the server down
            (log or emit)(f"failed: {type(e).__name__}: {e}")
            return
        if counts["assessed"]:
            ctx.bus.publish("system.updated", None, {"documents_reassessed": counts["assessed"], **counts})
    th = threading.Thread(target=run, name="aidoc-reassess", daemon=True)
    th.start()
    return th


def create_app(ctx: ServerContext) -> FastAPI:
    from aidoc.server import sse
    from aidoc.server.api import chunks, documents, jobs, settings, system, uploads
    # interactive docs only in loopback mode: with a token they would sit outside the token gate
    docs = not ctx.token
    app = FastAPI(title="aidoc", version="0.1.0", docs_url="/api/docs" if docs else None,
                  openapi_url="/api/openapi.json" if docs else None, redoc_url=None)
    app.state.ctx = ctx

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(exc.status, exc.error, **exc.extra)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        err = _ERROR_NAMES.get(exc.status_code) or (exc.detail if isinstance(exc.detail, str) else "http_error")
        return error_response(exc.status_code, err, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return error_response(422, "validation_error", detail=json.loads(json.dumps(exc.errors(), default=str)))

    api = APIRouter(prefix="/api", dependencies=[Depends(auth.require_token)])
    api.include_router(system.router)
    api.include_router(sse.router)
    api.include_router(jobs.router)
    api.include_router(uploads.router)
    api.include_router(documents.router)
    api.include_router(chunks.router)
    api.include_router(settings.router)
    app.include_router(api)
    _mount_web(app, Path(ctx.config.root) / "web" / "dist")
    app.add_middleware(EnvelopeMiddleware)
    return app


# The Web UI's own pages: scripts only from this origin ('wasm-unsafe-eval' for the hash-wasm upload worker),
# no plugins, no <base> rewriting, no framing. Styles stay open (KaTeX and Radix set inline styles).
SPA_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; "
                               "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
                               "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                               "form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._@+-]+$")


def _dist_files(dist: Path) -> dict[str, Path]:
    """Every file under web/dist keyed by its POSIX relative path (links are not followed)."""
    out: dict[str, Path] = {}
    if not dist.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(dist, followlinks=False):
        for name in filenames:
            full = Path(dirpath) / name
            if full.is_symlink():
                continue
            out[full.relative_to(dist).as_posix()] = full
    return out


def safe_relative(path: str) -> str | None:
    """`path` as a plain relative POSIX path of ordinary segments, else None. Pure string checks: backslashes,
    drive letters, colons, NUL, empty or dot segments and leading slashes are all refused, so a UNC or drive path
    can never reach the filesystem (P3 verifier #1: a UNC path made Windows open an SMB connection)."""
    if not path or "\\" in path or ":" in path or "\x00" in path or path.startswith("/"):
        return None
    parts = path.split("/")
    if any(p in ("", ".", "..") or not _SAFE_SEGMENT.match(p) for p in parts):
        return None
    return "/".join(parts)


def _mount_web(app: FastAPI, dist: Path) -> None:
    """Serve the built Web UI (P4) with an SPA fallback; /api/* never falls through to it.

    The request path is only ever looked up in a table of the files that exist under web/dist (rebuilt when
    index.html changes); it is never joined onto a filesystem path or resolved."""
    index = dist / "index.html"
    cache: dict = {"mtime": None, "files": {}}

    def files() -> dict[str, Path]:
        try:
            mtime = index.stat().st_mtime
        except OSError:
            return {}
        if cache["mtime"] != mtime:
            cache["files"], cache["mtime"] = _dist_files(dist), mtime
        return cache["files"]

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def web(full_path: str, request: Request):
        if not auth.host_ok(request, app.state.ctx):
            return error_response(403, "bad_host")
        if full_path == "api" or full_path.startswith("api/"):
            for route in app.router.routes:           # a known /api path with another method -> 405
                if route.matches(request.scope)[0] == Match.PARTIAL:
                    return error_response(405, "method_not_allowed")
            return error_response(404, "not_found")
        table = files()
        if not table:
            return {"message": "web UI not built; run pnpm --dir web build"}
        rel = safe_relative(full_path)
        if rel is not None and rel in table and rel != "index.html":
            return FileResponse(table[rel], headers={"X-Content-Type-Options": "nosniff"})
        return FileResponse(table["index.html"], media_type="text/html", headers=SPA_HEADERS)
