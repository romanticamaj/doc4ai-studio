"""FastAPI app factory (spec §6). Every JSON body carries `"workspace": "default"`."""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

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


def error_response(status: int, error: str, headers: dict | None = None, **extra) -> JSONResponse:
    return JSONResponse(envelope({"error": error, **extra}), status_code=status, headers=headers)


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
    return ctx


def create_app(ctx: ServerContext) -> FastAPI:
    from aidoc.server import sse
    from aidoc.server.api import chunks, documents, jobs, settings, system, uploads
    app = FastAPI(title="aidoc", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json",
                  redoc_url=None)
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
    app.add_middleware(EnvelopeMiddleware)
    return app
