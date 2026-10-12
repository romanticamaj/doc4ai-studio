"""`POST /api/verify` (spec 2026-10-05 verify §2.6, index §8): multipart upload, a Library document, or local paths
(loopback / token only). A thin shell around `aidoc.verify.verify_markdown` — it never writes the store."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request as StarletteRequest

from aidoc.server.auth import ApiError, allow_local_paths

router = APIRouter()

_JSON_MAX = 64 * 1024
_FORM_OVERHEAD = 1024 * 1024         # multipart headers + small fields on top of the two files
_SPOOL = 8 * 1024 * 1024


async def _bounded_body(request: Request, limit: int):
    """The request body spooled to a temp file, refusing more than `limit` bytes (413 too_large)."""
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > limit:
        raise ApiError(413, "too_large", limit=limit)
    spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL)   # noqa: SIM115  closed by the caller
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise ApiError(413, "too_large", limit=limit)
            spool.write(chunk)
    except BaseException:
        spool.close()
        raise
    spool.seek(0)
    return spool


def _int(v, name: str, default: int | None) -> int | None:
    if v in (None, ""):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ApiError(422, "invalid_input", detail=f"{name} must be an integer") from None


def _run(md: str, pdf: Path | None, *, pages, fmt, marker_regex, sample, previous, name, pdf_name=None) -> dict:
    from aidoc.verify import verify_markdown
    try:
        report = verify_markdown(md, pdf, pages=pages, fmt=fmt or "auto", marker_regex=marker_regex or None,
                                 sample=30 if sample is None else sample, previous=previous, input_name=name)
    except ValueError as e:                       # includes NeedsPagesError (no PDF, no pages)
        raise ApiError(422, "invalid_input", detail=str(e)) from None
    out = report.to_json()
    if pdf_name is not None and out["pdf"] is not None:
        out["pdf"]["path"] = pdf_name              # never echo the server's temp path
    return out


async def _multipart(request: Request) -> dict:
    ctx = request.app.state.ctx
    limit = ctx.config.limits.upload_max_bytes
    spool = await _bounded_body(request, 2 * limit + _FORM_OVERHEAD)
    tmpdir = tempfile.TemporaryDirectory(prefix="aidoc-verify-")
    try:
        async def receive():
            data = spool.read(1 << 20)
            return {"type": "http.request", "body": data, "more_body": bool(data)}
        form = await StarletteRequest(request.scope, receive).form(max_files=2, max_fields=20)
        try:
            md_part, pdf_part = form.get("markdown"), form.get("pdf")
            if not isinstance(md_part, UploadFile):
                raise ApiError(422, "invalid_input", detail="send the Markdown file in the multipart field 'markdown'")
            for part in (md_part, pdf_part):
                if isinstance(part, UploadFile) and (part.size or 0) > limit:
                    raise ApiError(413, "too_large", limit=limit)
            try:
                md = (await md_part.read()).decode("utf-8-sig")
            except UnicodeDecodeError:
                raise ApiError(422, "invalid_input", detail="the Markdown must be UTF-8") from None
            pdf = None
            if isinstance(pdf_part, UploadFile):
                pdf = Path(tmpdir.name) / "source.pdf"
                pdf.write_bytes(await pdf_part.read())
            md_name = Path(md_part.filename or "markdown.md").name
            pdf_name = Path(pdf_part.filename or "source.pdf").name if pdf is not None else None
            return await run_in_threadpool(
                _run, md, pdf, pages=_int(form.get("pages"), "pages", None), fmt=form.get("format"),
                marker_regex=form.get("marker_regex"), sample=_int(form.get("sample"), "sample", 30),
                previous=None, name=md_name, pdf_name=pdf_name)
        finally:
            await form.close()
    finally:
        spool.close()
        tmpdir.cleanup()


async def _json_body(request: Request) -> dict:
    spool = await _bounded_body(request, _JSON_MAX)
    try:
        body = json.loads(spool.read() or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError(422, "invalid_input", detail="invalid JSON body") from None
    finally:
        spool.close()
    if not isinstance(body, dict):
        raise ApiError(422, "invalid_input", detail="send a JSON object")
    ctx = request.app.state.ctx
    opts = {"pages": _int(body.get("pages"), "pages", None), "fmt": body.get("format"),
            "marker_regex": body.get("marker_regex"), "sample": _int(body.get("sample"), "sample", 30)}
    has_doc, has_path = "doc_id" in body, "markdown_path" in body
    if has_doc == has_path:
        raise ApiError(422, "invalid_input", detail="send exactly one of doc_id or markdown_path (+ pdf_path)")
    if has_doc:
        from aidoc.verify import OutputMissing, document_inputs
        doc = ctx.store.get_document(str(body["doc_id"]))
        if doc is None:
            raise ApiError(404, "not_found")
        try:
            md, pdf, previous, pages, name = document_inputs(doc)
        except OutputMissing:
            raise ApiError(409, "output_missing") from None
        return await run_in_threadpool(_run, md, pdf, **{**opts, "pages": opts["pages"] or (pages if pdf is None
                                                                                          else None)},
                                       previous=previous, name=name)
    if not allow_local_paths(request, ctx):
        raise ApiError(403, "local_path_forbidden")
    md_path = Path(str(body["markdown_path"]))
    pdf_path = Path(str(body["pdf_path"])) if body.get("pdf_path") else None
    if not md_path.is_file():
        raise ApiError(422, "invalid_input", detail=f"markdown_path not found: {md_path}")
    if pdf_path is not None and not pdf_path.is_file():
        raise ApiError(422, "invalid_input", detail=f"pdf_path not found: {pdf_path}")
    try:
        md = md_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        raise ApiError(422, "invalid_input", detail=f"cannot read markdown_path: {e}") from None
    return await run_in_threadpool(_run, md, pdf_path, **opts, previous=None, name=str(md_path))


@router.post("/verify")
async def verify(request: Request) -> dict:
    ctype = (request.headers.get("content-type") or "").lower()
    if ctype.startswith("multipart/form-data"):
        return await _multipart(request)
    return await _json_body(request)
