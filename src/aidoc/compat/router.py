"""Routes of the docling-serve compatible API (compat spec §4.1). Mounted at the app root before the SPA catch-all;
never under /api, never enveloped."""
from __future__ import annotations

import json
import os
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from aidoc.compat import jobs
from aidoc.compat.auth import (
    CompatError,
    compat_principal,
    compat_principal_assets,
    compat_principal_read,
    new_call,
    require_enabled,
)
from aidoc.compat.options import parse_options
from aidoc.mcp.errors import ToolFailure
from aidoc.mcp.principal import Principal
from aidoc.mcp.tools_convert import decode_content

MIB = 1024 * 1024

router = APIRouter()

TO_FORMATS = ["md", "text"]
OCR_LANGS = ["zh-Hant", "en"]
FROM_FORMATS = ["pdf", "docx", "pptx", "xlsx", "doc", "ppt", "xls", "html", "md", "csv", "image", "epub", "xml_jats", "json_docling"]


@router.get("/v1/capabilities", dependencies=[Depends(require_enabled)])
def capabilities(request: Request):
    """Unauthenticated like docling-serve's; static apart from engine readiness booleans (plan A4)."""
    from aidoc import __version__
    from aidoc.engines.registry import get_engines
    ctx = request.app.state.ctx
    call = new_call(request)
    call.tool_name = "capabilities"
    cfg = ctx.config
    return JSONResponse({
        "from_formats": FROM_FORMATS, "to_formats": TO_FORMATS, "ocr_langs": OCR_LANGS,
        "engines": {name: bool(e.available()) for name, e in get_engines(cfg).items()},
        "doc4ai": {"version": __version__, "max_file_mb": cfg.compat.max_file_mb,
                   "max_source_mb": cfg.compat.max_source_mb, "sync_timeout_s": cfg.compat.sync_timeout_s}},
        headers={"Cache-Control": "no-store"})


def _rate_limit(ctx, principal: Principal, call) -> None:
    """Convert requests count against the token's tools/call bucket (spec §4.3 step 1)."""
    ok, retry = ctx.extras["mcp"].limiter.acquire(principal.token_id or principal.subject, principal.rate_limit_per_min)
    if not ok:
        call.fail("rate_limited", "rate_limited")
        raise CompatError(429, "rate limited", headers={"Retry-After": str(retry)}, retry_after=retry)


JSON_MAX_DEPTH = 32


def _depth(value) -> int:
    """Nesting depth of decoded JSON, iteratively (no recursion on hostile input)."""
    deepest, stack = 0, [(value, 1)]
    while stack:
        v, d = stack.pop()
        deepest = max(deepest, d)
        if d > JSON_MAX_DEPTH:
            return d
        if isinstance(v, dict):
            stack.extend((x, d + 1) for x in v.values())
        elif isinstance(v, list):
            stack.extend((x, d + 1) for x in v)
    return deepest


async def _read_json(request: Request, call, max_mb: int) -> dict:
    declared = request.headers.get("content-length")
    if declared and declared.strip().isdigit() and int(declared) > max_mb * MIB:
        raise jobs.too_large(call, max_mb)
    body, size = bytearray(), 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_mb * MIB:
            raise jobs.too_large(call, max_mb)
        body += chunk
    try:
        data = json.loads(bytes(body) or b"null")
    except (ValueError, RecursionError):
        raise CompatError(422, [{"type": "json_invalid", "loc": ["body"], "msg": "body is not JSON"}]) from None
    if _depth(data) > JSON_MAX_DEPTH:                     # verifier item 4: never hand deep structures onwards
        raise CompatError(422, [{"type": "json_invalid", "loc": ["body"], "msg": f"nested deeper than {JSON_MAX_DEPTH}"}])
    if not isinstance(data, dict):
        raise CompatError(422, [{"type": "dict_type", "loc": ["body"], "msg": "body must be a JSON object"}])
    return data


def _source_upload(ctx, call, body: dict, *, alpha: bool) -> jobs.Upload:
    """The one `sources[kind=file]` (v1) or `file_sources[]` (v1alpha) entry, decoded to a part file."""
    target = body.get("target")
    if isinstance(target, dict) and target.get("kind") not in (None, "inbody"):
        raise CompatError(422, f"unsupported_target: {target.get('kind')}")
    if alpha and body.get("http_sources"):
        raise CompatError(422, "unsupported_source: http")
    if alpha and "sources" not in body:
        sources = [{**s, "kind": "file"} if isinstance(s, dict) else s for s in (body.get("file_sources") or [])]
    else:
        sources = body.get("sources")
    if not isinstance(sources, list) or not sources:
        raise CompatError(422, [{"type": "missing", "loc": ["body", "sources"], "msg": "one source of kind 'file' is required"}])
    if len(sources) > 1:
        raise CompatError(422, "one source per request")
    src = sources[0]
    if not isinstance(src, dict):
        raise CompatError(422, [{"type": "dict_type", "loc": ["body", "sources", 0], "msg": "must be an object"}])
    if src.get("kind", "file") != "file":
        raise CompatError(422, f"unsupported_source: {src.get('kind')}")
    b64 = src.get("base64_string")
    if not isinstance(b64, str) or not b64:
        raise CompatError(422, [{"type": "missing", "loc": ["body", "sources", 0, "base64_string"], "msg": "required"}])
    limit_mb = ctx.config.compat.max_file_mb
    try:
        data = decode_content(b64, limit_mb * MIB)
    except ToolFailure as f:
        if f.code == "file_too_large":
            raise jobs.too_large(call, limit_mb) from None
        call.fail(f.code)
        raise CompatError(422, [{"type": f.code, "loc": ["body", "sources", 0, "base64_string"], "msg": f.message}]) from None
    filename = src.get("filename") if isinstance(src.get("filename"), str) else "document"
    return jobs.write_part(ctx, data, filename)


@router.post("/v1/convert/file")
async def convert_file(request: Request, principal: Annotated[Principal, Depends(compat_principal)]):
    ctx = request.app.state.ctx
    call = request.state.compat
    call.tool_name = "convert_file"
    _rate_limit(ctx, principal, call)
    upload = await jobs.stream_upload(request, call, max_mb=ctx.config.compat.max_file_mb)
    try:
        opts = parse_options(upload.fields, ctx.config, multipart=True)
    except BaseException:
        upload.part_path.unlink(missing_ok=True)
        raise
    return await jobs.run_sync(ctx, request, call, upload, opts)


async def _convert_source(request: Request, principal: Principal, *, alpha: bool):
    ctx = request.app.state.ctx
    call = request.state.compat
    call.tool_name = "convert_source"
    _rate_limit(ctx, principal, call)
    body = await _read_json(request, call, ctx.config.compat.max_source_mb)
    opts = parse_options(body.get("options") or {}, ctx.config, multipart=False)
    upload = _source_upload(ctx, call, body, alpha=alpha)
    return await jobs.run_sync(ctx, request, call, upload, opts)


@router.post("/v1/convert/source")
async def convert_source(request: Request, principal: Annotated[Principal, Depends(compat_principal)]):
    return await _convert_source(request, principal, alpha=False)


@router.post("/v1alpha/convert/source")
async def convert_source_v1alpha(request: Request, principal: Annotated[Principal, Depends(compat_principal)]):
    """RAGFlow's fallback path (spike S1): the old `file_sources` shape (or `sources`); same response as v1."""
    return await _convert_source(request, principal, alpha=True)


@router.post("/v1/convert/file/async")
async def convert_file_async(request: Request, principal: Annotated[Principal, Depends(compat_principal)]):
    ctx = request.app.state.ctx
    call = request.state.compat
    call.tool_name = "convert_file_async"
    _rate_limit(ctx, principal, call)
    upload = await jobs.stream_upload(request, call, max_mb=ctx.config.compat.max_file_mb)
    try:
        opts = parse_options(upload.fields, ctx.config, multipart=True)
    except BaseException:
        upload.part_path.unlink(missing_ok=True)
        raise
    return await jobs.submit_async(ctx, request, call, upload, opts)


@router.post("/v1/convert/source/async")
async def convert_source_async(request: Request, principal: Annotated[Principal, Depends(compat_principal)]):
    ctx = request.app.state.ctx
    call = request.state.compat
    call.tool_name = "convert_source_async"
    _rate_limit(ctx, principal, call)
    body = await _read_json(request, call, ctx.config.compat.max_source_mb)
    opts = parse_options(body.get("options") or {}, ctx.config, multipart=False)
    upload = _source_upload(ctx, call, body, alpha=False)
    return await jobs.submit_async(ctx, request, call, upload, opts)


@router.get("/v1/status/poll/{task_id}")
async def status_poll(request: Request, task_id: str, principal: Annotated[Principal, Depends(compat_principal_read)],
                      wait: float = 0.0):
    call = request.state.compat
    call.tool_name = "status_poll"
    call.args = {"task_id": task_id, "wait": wait}
    return await jobs.poll(request.app.state.ctx, call, task_id, wait)


@router.get("/v1/result/{task_id}")
def task_result(request: Request, task_id: str, principal: Annotated[Principal, Depends(compat_principal_read)]):
    call = request.state.compat
    call.tool_name = "result"
    call.args = {"task_id": task_id}
    return jobs.result(request.app.state.ctx, request, call, task_id)


@router.get("/v1/doc4ai/documents/{doc_id}/assets/{path:path}")
def document_asset(request: Request, doc_id: str, path: str,
                   principal: Annotated[Principal, Depends(compat_principal_assets)]):
    """Where image_export_mode=referenced links point (Doc4AI extension). Same path rules and safe headers as
    /api/documents/{id}/assets (nosniff, CSP sandbox, inline only for images)."""
    from pathlib import Path

    from aidoc.compat.render import asset_file
    from aidoc.server.api.documents import safe_file_response
    from aidoc.server.app import safe_relative
    ctx = request.app.state.ctx
    call = request.state.compat
    call.tool_name = "asset"
    call.args = {"doc_id": doc_id[:64], "name": path[:200]}
    doc = ctx.store.get_document(doc_id)
    if doc is None or doc["status"] not in jobs.VISIBLE:
        raise CompatError(404, "document not found")
    if safe_relative(path) is None:
        raise CompatError(400, "bad path")
    target = asset_file(Path(doc["output_dir"]) / "assets", path)    # no links out of assets/ (verifier item 3)
    if target is None:
        raise CompatError(400, "bad path")
    if not target.is_file():
        raise CompatError(404, "asset not found")
    return safe_file_response(target, target.name, request)


def _whoami(request: Request, principal: Annotated[Principal, Depends(compat_principal)]):
    request.state.compat.tool_name = "_whoami"
    return {"token_id": principal.token_id}


def install(app) -> None:
    """Mount the compat routes on the app (before the SPA catch-all): the router, the tests-only `/v1/_whoami`
    (AIDOC_TEST_ROUTES=1, read when the app is built) and docling-style 404s for unknown /v1, /v1alpha paths."""
    app.include_router(router)
    if os.environ.get("AIDOC_TEST_ROUTES") == "1":
        app.add_api_route("/v1/_whoami", _whoami, methods=["GET"], include_in_schema=False)

    async def not_found(request: Request, path: str):
        raise CompatError(404, "Not Found")
    for prefix in ("/v1", "/v1alpha"):
        app.add_api_route(prefix + "/{path:path}", not_found, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
                          include_in_schema=False)
