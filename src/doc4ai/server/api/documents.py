"""Library / Document endpoints (index §8, spec §6 pages 3-4, §8.5 orphaned)."""
from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from doc4ai import fsops
from doc4ai.models import ConvertOptions
from doc4ai.output import read_sidecar, sidecar_path
from doc4ai.server.auth import ApiError
from doc4ai.server.serialize import serialize_document, serialize_job
from doc4ai.server.transfer import etag_matches, file_etag
from doc4ai.sources import document_source, resolve_source

router = APIRouter()


def _doc(request: Request, doc_id: str) -> dict:
    doc = request.app.state.ctx.store.get_document(doc_id)
    if doc is None:
        raise ApiError(404, "not_found")
    return doc


source_file = document_source


def output_available(doc: dict) -> bool:
    out = Path(doc["output_dir"])
    return sidecar_path(out).is_file() and (out / f"{out.name}.md").is_file()


@router.get("/documents")
def list_documents(request: Request, status: str | None = None, engine: str | None = None,
                   q: str | None = None,
                   flag: Literal["page_map_incomplete", "page_quality", "unassessed",
                                 "source_not_copied"] | None = None) -> dict:
    store = request.app.state.ctx.store
    docs = store.list_documents(status=status or None, engine=engine or None, q=q or None, flag=flag)
    return {"documents": [serialize_document(d) for d in docs]}


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, request: Request) -> dict:
    doc = _doc(request, doc_id)
    from doc4ai.server.api.sources import copy_view
    found = resolve_source(doc, store=request.app.state.ctx.store)
    doc = request.app.state.ctx.store.get_document(doc_id) or doc          # a drift refresh may have updated it
    return {"document": serialize_document(doc), "source_available": found is not None,
            "source_kind": found.kind if found is not None else None, "source_copy": copy_view(doc, request.app.state.ctx.config),
            "output_available": output_available(doc), "sidecar": read_sidecar(Path(doc["output_dir"])),
            "preview": _preview_status(request, doc)}


# ---- Office preview (LibreOffice -> PDF, doc4ai.preview): the left pane of the Document view

def _is_office_doc(doc: dict) -> bool:
    from doc4ai.preview import is_office
    return is_office(Path(doc["source_path"]).name)


def _preview_status(request: Request, doc: dict) -> dict | None:
    if not _is_office_doc(doc):
        return None
    return request.app.state.ctx.extras["preview"].status(doc)


class PreviewIn(BaseModel):
    retry: bool = False


@router.post("/documents/{doc_id}/preview")
async def request_preview(doc_id: str, request: Request) -> Response:
    """Queue the PDF preview of an Office document (on demand; one soffice run at a time, never on the job queue).
    202 while it is pending, 200 when it is already ready or cannot be made (status failed + reason). The SSE event
    `document.preview` {document_id, status, pages | reason} follows when a pending preview finishes."""
    from fastapi.responses import JSONResponse
    doc = _doc(request, doc_id)
    raw = await request.body()
    body = PreviewIn.model_validate_json(raw) if raw.strip() else PreviewIn()
    if not _is_office_doc(doc):
        raise ApiError(409, "not_office")
    st = request.app.state.ctx.extras["preview"].request(doc, retry=body.retry)
    return JSONResponse({"preview": st}, status_code=202 if st["status"] == "pending" else 200)


def _preview_file(request: Request, doc_id: str) -> Path:
    doc = _doc(request, doc_id)
    if not _is_office_doc(doc):
        raise ApiError(409, "not_office")
    mgr = request.app.state.ctx.extras["preview"]
    pdf = mgr.pdf_path(doc)
    if pdf is None or not pdf.is_file():
        raise ApiError(409, "preview_not_ready", preview=mgr.status(doc))
    return pdf


@router.get("/documents/{doc_id}/preview")
def get_preview(doc_id: str, request: Request) -> Response:
    """The preview PDF, served exactly like a PDF source (inline, nosniff, sandbox CSP, ETag, byte ranges)."""
    pdf = _preview_file(request, doc_id)
    return safe_file_response(pdf, "preview.pdf", request)


@router.get("/documents/{doc_id}/preview/open")
def get_preview_open(doc_id: str, request: Request,
                     chunk: int = Query(..., ge=1024, le=4 * 1024 * 1024)) -> Response:
    return _open_bundle(_preview_file(request, doc_id), request, chunk)


@router.get("/documents/{doc_id}/markdown")
def get_markdown(doc_id: str, request: Request) -> Response:
    doc = _doc(request, doc_id)
    out = Path(doc["output_dir"])
    md = out / f"{out.name}.md"
    if not md.is_file():
        raise ApiError(410, "output_missing")
    body = md.read_bytes()
    etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    if _etag_matches(request, etag):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": REVALIDATE})
    return Response(body, media_type="text/markdown; charset=utf-8",
                    headers={"ETag": etag, "Cache-Control": REVALIDATE})


@router.get("/documents/{doc_id}/assets/{path:path}")
def get_asset(doc_id: str, path: str, request: Request) -> Response:
    doc = _doc(request, doc_id)
    base = (Path(doc["output_dir"]) / "assets").resolve()
    rel = path.replace("\\", "/")
    if not rel or rel.startswith("/") or ":" in rel or "\x00" in rel or any(part in ("..", "") for part in rel.split("/")):
        raise ApiError(400, "bad_path")
    target = (base / rel).resolve()
    if os.path.commonpath([str(base), str(target)]) != str(base):
        raise ApiError(400, "bad_path")
    if not target.is_file():
        raise ApiError(404, "not_found")
    return safe_file_response(target, target.name, request)


@router.get("/documents/{doc_id}/source")
def get_source(doc_id: str, request: Request) -> Response:
    doc = _doc(request, doc_id)
    f = source_file(doc)
    if f is None:
        raise ApiError(410, "source_missing")
    name = Path(doc["source_path"]).name or f.name
    return safe_file_response(f, name, request)


PDF_RANGES_TYPE = "application/vnd.aidoc.pdf-ranges"
PREFETCH_VERSION = "1"


@router.get("/documents/{doc_id}/source/open")
def get_source_open(doc_id: str, request: Request,
                    chunk: int = Query(..., ge=1024, le=4 * 1024 * 1024)) -> Response:
    """The byte ranges pdf.js needs to open the source PDF and draw its first pages, in one response (one round
    trip instead of one per xref section and object; doc4ai.pdfprefetch). Body: those ranges back to back; header
    X-Pdf-Ranges lists them (`begin-end`, end exclusive, aligned to `chunk` = the viewer's rangeChunkSize) and
    X-Pdf-Size gives the file size. Revalidated like the source itself."""
    doc = _doc(request, doc_id)
    f = source_file(doc)
    if f is None:
        raise ApiError(410, "source_missing")
    return _open_bundle(f, request, chunk)


def _open_bundle(f: Path, request: Request, chunk: int) -> Response:
    from doc4ai.pdfprefetch import open_ranges
    with f.open("rb") as fh:
        if fh.read(1024).find(b"%PDF-") < 0:
            raise ApiError(415, "not_pdf")
    etag = '"' + hashlib.sha256(f"{file_etag(f)}-{chunk}-{PREFETCH_VERSION}".encode()).hexdigest()[:32] + '"'
    caching = {"ETag": etag, "Cache-Control": REVALIDATE}
    if _etag_matches(request, etag):
        return Response(status_code=304, headers={**FILE_HEADERS, **caching})
    size = f.stat().st_size
    ranges = open_ranges(f, chunk)

    def body():
        with f.open("rb") as fh:
            for b, e in ranges:
                fh.seek(b)
                left = e - b
                while left > 0:
                    data = fh.read(min(left, 1 << 20))
                    if not data:
                        return
                    left -= len(data)
                    yield data

    return StreamingResponse(body(), media_type=PDF_RANGES_TYPE, headers={
        **FILE_HEADERS, **caching, "Content-Length": str(sum(e - b for b, e in ranges)), "X-Pdf-Size": str(size),
        "X-Pdf-Ranges": ",".join(f"{b}-{e}" for b, e in ranges)})


# Converted documents come from anywhere: a source or asset must never run as a page on the doc4ai origin (final
# P4 check C1: an HTML source served inline read the API token from localStorage). Only types a browser cannot
# execute are shown inline; everything else downloads as opaque bytes, and every file response is sandboxed.
SAFE_INLINE = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/tiff"}
FILE_HEADERS = {"X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'"}


# A reconversion rewrites outputs under the same names (assets/p1_1.png): every file is revalidated on each use,
# with a strong validator, so a browser never shows a pre-repair image or text from its cache (verifier finding).
REVALIDATE = "no-cache"


_file_etag = file_etag


def _etag_matches(request: Request | None, etag: str) -> bool:
    # weak comparison: a gzipped response carried W/"<tag>" (transfer.CompressionMiddleware)
    return request is not None and etag_matches(request.headers.get("if-none-match"), etag)


def safe_file_response(path: Path, filename: str, request: Request | None = None) -> Response:
    etag = _file_etag(path)
    caching = {"ETag": etag, "Cache-Control": REVALIDATE}
    if _etag_matches(request, etag):
        return Response(status_code=304, headers={**FILE_HEADERS, **caching})
    mime = mimetypes.guess_type(filename)[0] or mimetypes.guess_type(path.name)[0] or ""
    if mime in SAFE_INLINE:
        return FileResponse(path, media_type=mime, headers={**FILE_HEADERS, **caching,
                                                            "Content-Disposition": content_disposition(filename,
                                                                                                       "inline")})
    return FileResponse(path, media_type="application/octet-stream",
                        headers={**FILE_HEADERS, **caching, "Content-Disposition": content_disposition(filename)})


@router.get("/documents/{doc_id}/download.zip")
def download_zip(doc_id: str, request: Request) -> Response:
    doc = _doc(request, doc_id)
    out = Path(doc["output_dir"])
    if not out.is_dir():
        raise ApiError(410, "output_missing")
    spool = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)   # noqa: SIM115  closed by the iterator
    with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as z:
        for f in _plain_files(out):
            z.write(f, f.relative_to(out).as_posix())
    spool.seek(0)

    def chunks():
        try:
            while block := spool.read(1024 * 1024):
                yield block
        finally:
            spool.close()
    return StreamingResponse(chunks(), media_type="application/zip",
                             headers={"Content-Disposition": content_disposition(f"{out.name}.zip")})


def content_disposition(filename: str, kind: str = "attachment") -> str:
    """RFC 6266 / 5987: an ASCII fallback plus `filename*` in UTF-8 (a raw non-Latin-1 name made a 500)."""
    fallback = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in filename) or "download"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _plain_files(root: Path) -> list[Path]:
    """Regular files under `root`, never following symlinks or junctions out of it (P3 verifier #6)."""
    base = root.resolve()
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        dirnames[:] = sorted(n for n in dirnames if not _is_link(d / n))
        for name in sorted(filenames):
            f = d / name
            if _is_link(f) or not f.is_file():
                continue
            if os.path.commonpath([str(base), str(f.resolve())]) != str(base):
                continue
            out.append(f)
    return out


def _is_link(p: Path) -> bool:
    try:
        if p.is_symlink():
            return True
        is_junction = getattr(os.path, "isjunction", None)
        return bool(is_junction and is_junction(p))
    except OSError:
        return True


@router.delete("/documents/orphaned")
def delete_orphaned(request: Request) -> dict:
    """Purged records take their source copies with them (spec 2026-10-29 §8): each document deletes only its own
    copy path (a hardlink shared with a live document keeps the data); a copy that cannot be deleted (open
    elsewhere) is listed in source_copies_kept and never blocks the purge."""
    from doc4ai.source_copy import is_present, remove_copy
    store = request.app.state.ctx.store
    deleted_copies, freed, kept = 0, 0, []
    for doc in store.list_documents(status="orphaned"):
        scp = doc.get("source_copy_path")
        if scp:
            try:
                existed = is_present(scp)
                freed += remove_copy(Path(scp))
                deleted_copies += 1 if existed else 0
            except OSError:
                kept.append(doc["id"])
        wc = doc.get("work_copy_path")
        if wc:
            try:
                fsops.remove_tree(Path(wc))
            except OSError:
                pass
    return {"deleted": store.delete_documents("orphaned"), "source_copies_deleted": deleted_copies,
            "source_copy_bytes": freed, "source_copies_kept": kept}


@router.post("/documents/rescan")
def rescan(request: Request, all: int = 0) -> dict:
    """Mark documents whose output (sidecar) is gone as orphaned (index A17); restore ones whose output is back;
    re-assess page map / page quality of documents never assessed (or all with ?all=1; spec 2026-10-01 §11)."""
    ctx = request.app.state.ctx
    store = ctx.store
    n = 0
    for doc in store.list_documents():
        present = sidecar_path(Path(doc["output_dir"])).is_file()
        if doc["status"] in ("ok", "warn", "low") and not present:
            store.set_document_status(doc["id"], "orphaned")
            n += 1
        elif doc["status"] == "orphaned" and present:
            level = (doc.get("quality") or {}).get("level")
            store.set_document_status(doc["id"], level if level in ("ok", "warn", "low") else "ok")
    from doc4ai.reassess import reassess_all
    counts = reassess_all(store, force=bool(all), log=lambda line: None)
    ctx.bus.publish("system.updated", None, {"documents_rescanned": True, "orphaned": n, **counts})
    return {"orphaned": n, **counts}


class ReconvertIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


def _stage_for_reconvert(src: Path, dst: Path, *, link: bool) -> None:
    """Put the source into the new task's work dir. A work copy may be hardlinked (it is ours and never written
    to); a source copy is always really copied (main spec §8.5: the user may edit files under source/, and a shared
    inode would carry that into the work dir)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.tmp")
    tmp.unlink(missing_ok=True)
    try:
        if not link:
            raise OSError("a real copy is required")
        os.link(src, tmp)
    except OSError:
        shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def start_reconvert(ctx, ids: list[str], *, origin: str = "web", engine: str | None = None, create_job=None) -> dict:
    """One job, one forced task per document, written back to the document's own output dir (spec 2026-10-01
    §9.2). Every id is checked before anything is created; raises ApiError 404/410/409. `engine` forces that
    engine (MCP reconvert_document); None lets routing choose (the web 「重新轉換」)."""
    store = ctx.store
    plan = []
    for doc_id in ids:
        doc = store.get_document(doc_id)
        if doc is None:
            raise ApiError(404, "not_found", id=doc_id)
        found = resolve_source(doc, store=store)        # work copy -> source copy -> original (index A27)
        if found is None:
            raise ApiError(410, "source_missing", id=doc_id)
        busy = store.busy_task_id(doc["sha256"], doc["output_dir"])
        if busy is None:
            live = [t for t in store.list_tasks() if t["sha256"] == doc["sha256"] and t["output_dir"] == doc["output_dir"]
                    and t["status"] in ("queued", "probing", "converting", "checking")]
            busy = live[0]["id"] if live else None
        if busy is not None:
            raise ApiError(409, "already_converting", id=doc_id, task_id=busy)
        plan.append((doc, found.path, found.kind))
    cfg = ctx.config
    out_root = Path(plan[0][0]["output_dir"]).parent
    opts = ConvertOptions(output_dir=out_root, engine=engine, lang=plan[0][0].get("lang") or cfg.general.lang, force=True,
                          allow_online_audio=bool(cfg.general.enable_audio), mineru_tier=cfg.engines.mineru_tier,
                          docling_ocr=cfg.engines.docling_ocr)
    # `create_job(opts) -> job_id`: MCP passes its per-token-capped reservation (tools_convert.reserve_job)
    job_id = create_job(opts) if create_job is not None else store.create_job(opts, origin)
    task_ids = []
    try:
        for doc, src, kind in plan:
            st = src.stat()
            tid, reused = store.create_task(job_id, doc["source_path"], doc["sha256"], st.st_size, st.st_mtime,
                                            doc.get("lang") or opts.lang, doc["output_dir"])
            if reused:
                # an unfinished earlier run (e.g. a cancelled reconvert) is not resumed: a reconvert starts over, so
                # routing begins again with the first engine instead of the engine whose segments were left done
                store.requeue_task(tid, reset_segments=True)
                store.update_task(tid, tried=[], attempt=0, engine=None, quality=None)
            work_path = None
            if kind != "original":               # stage it into the new task's work dir, like an upload
                work_path = cfg.data_dir / "work" / tid / f"src{src.suffix.lower()}"
                _stage_for_reconvert(src, work_path, link=kind == "work_copy")
                store.update_task(tid, work_path=str(work_path))
            flags = {"force": True, "reconvert": True}
            if engine is None:
                flags["auto_engine"] = True
            store.set_task_flags(tid, flags)
            task_ids.append(tid)
    except BaseException:
        # a half-built job must not run, nor hold a per-token MCP slot (a task-less job stays "queued" forever)
        store.cancel_queued_tasks(job_id)
        store.set_job_status(job_id, "cancelled")
        raise
    store.refresh_job_status(job_id)
    for tid in task_ids:
        ctx.queue.publish_task(tid)
    ctx.queue.publish_job(job_id)
    ctx.queue.publish_queue()
    ctx.queue.wake()
    return serialize_job(store, store.get_job(job_id))


@router.post("/documents/reconvert", status_code=201)
def reconvert(body: ReconvertIn, request: Request) -> dict:
    return {"job": start_reconvert(request.app.state.ctx, list(dict.fromkeys(body.ids)))}
