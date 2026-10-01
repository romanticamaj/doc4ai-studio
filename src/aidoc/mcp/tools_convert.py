"""convert_document / convert_path (MCP spec §5.3): the same job queue the web UI uses, origin "mcp"."""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import shutil
import time
from pathlib import Path
from typing import Annotated, Literal

import anyio
from mcp.server.mcpserver import Context
from pydantic import Field

from aidoc.batch import register_source
from aidoc.mcp.errors import ToolFailure
from aidoc.mcp.principal import SCOPE_CONVERT, SCOPE_CONVERT_LOCAL, current_principal
from aidoc.mcp.registry import doc4ai_tool
from aidoc.mcp.schemas import ConvertOut
from aidoc.models import ConvertOptions, TaskStatus
from aidoc.names import file_sha256
from aidoc.output import lookup_cached, planned_output_dir
from aidoc.server.serialize import serialize_task
from aidoc.server.uploads import UploadError, display_name

ALLOWED_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp", ".docx", ".xlsx", ".pptx", ".doc",
                ".xls", ".ppt", ".html", ".htm", ".md", ".txt", ".csv", ".json", ".xml", ".epub"}
_MAGIC = {".pdf": (b"%PDF",), ".png": (b"\x89PNG",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",), ".gif": (b"GIF8",),
          ".webp": (b"RIFF",), ".tif": (b"II*\x00", b"MM\x00*"), ".tiff": (b"II*\x00", b"MM\x00*"), ".bmp": (b"BM",),
          ".docx": (b"PK\x03\x04",), ".xlsx": (b"PK\x03\x04",), ".pptx": (b"PK\x03\x04",), ".epub": (b"PK\x03\x04",),
          ".doc": (b"\xd0\xcf\x11\xe0",), ".xls": (b"\xd0\xcf\x11\xe0",), ".ppt": (b"\xd0\xcf\x11\xe0",)}
_BINARY_SIGS = tuple(sig for sigs in _MAGIC.values() for sig in sigs)
_DATA_PREFIX = re.compile(r"^data:[^,]*;base64,", re.IGNORECASE)
_WS = re.compile(r"\s+")
_TERMINAL = {s.value for s in (TaskStatus.done, TaskStatus.low, TaskStatus.failed, TaskStatus.skipped, TaskStatus.cancelled)}


def decode_content(content_base64: str, limit_bytes: int) -> bytes:
    s = _WS.sub("", _DATA_PREFIX.sub("", content_base64.strip()))
    if len(s) * 3 // 4 > limit_bytes:
        raise ToolFailure("file_too_large", f"the file is larger than the {limit_bytes // (1024 * 1024)} MB limit",
                          hint="split the document or raise mcp.max_upload_mb", limit_bytes=limit_bytes)
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        raise ToolFailure("invalid_base64", "content_base64 is not valid base64", hint="send standard or URL-safe base64 of the raw file bytes") from None


def check_magic(filename: str, data: bytes) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise ToolFailure("unsupported_file", f"{ext or 'no extension'} is not a supported input type",
                          hint="supported: " + ", ".join(sorted(ALLOWED_EXTS)))
    sigs = _MAGIC.get(ext)
    if sigs is not None:
        if not any(data.startswith(s) for s in sigs) or (ext == ".webp" and data[8:12] != b"WEBP"):
            raise ToolFailure("unsupported_file", f"the bytes do not look like a {ext} file", hint="check the file and its extension")
    elif any(data.startswith(s) for s in _BINARY_SIGS):               # text extension with binary content (A-M10)
        raise ToolFailure("unsupported_file", f"the bytes are a binary file but the name says {ext}", hint="use the real extension")


def build_convert_options(cfg, *, engine, lang, force) -> ConvertOptions:
    return ConvertOptions(output_dir=cfg.output_root(), engine=engine, lang=lang or cfg.general.lang, force=force,
                          allow_online_audio=bool(cfg.general.enable_audio), mineru_tier=cfg.engines.mineru_tier,
                          docling_ocr=cfg.engines.docling_ocr)


def _too_many(cfg) -> ToolFailure:
    return ToolFailure("too_many_jobs", "this token already has the maximum number of conversions in progress",
                       hint="wait for get_job to report done", limit=cfg.mcp.max_concurrent_jobs_per_token)


def reserve_job(ctx, opts: ConvertOptions, token_id: str | None) -> str:
    """Create an MCP job, checking the per-token cap atomically (Store.create_mcp_job). Every MCP-originated job
    goes through here (convert_document, convert_path, reconvert_document)."""
    if token_id is None:
        return ctx.store.create_job(opts, "mcp")
    jid = ctx.store.create_mcp_job(opts, token_id=token_id, limit=ctx.config.mcp.max_concurrent_jobs_per_token)
    if jid is None:
        raise _too_many(ctx.config)
    return jid


def _abandon(ctx, job_id: str) -> None:
    """A reserved job whose task could not be created must not hold a slot."""
    ctx.store.set_job_status(job_id, "cancelled")


def _guard_capacity(ctx, token_id: str | None, size: int) -> None:
    cfg = ctx.config
    if token_id and ctx.store.active_mcp_jobs(token_id) >= cfg.mcp.max_concurrent_jobs_per_token:
        raise _too_many(cfg)                    # early exit before any file is written; the binding check is atomic
    up = ctx.uploads.dir
    up.mkdir(parents=True, exist_ok=True)
    needed = size * cfg.limits.disk_space_factor
    free = shutil.disk_usage(up).free
    if free < needed:
        raise ToolFailure("insufficient_disk", "not enough free disk for this conversion", needed=needed, free=free)


def start_job_for_bytes(ctx, filename: str, data: bytes, opts: ConvertOptions, token_id: str | None = None) -> tuple[str, str]:
    store = ctx.store
    job_id = reserve_job(ctx, opts, token_id)
    try:
        name = display_name(filename)
        sha = hashlib.sha256(data).hexdigest()
        uid = store.create_upload(name, len(data), sha)
        part = ctx.uploads.part_path(uid)
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(data)
        store.update_upload(uid, received=len(data), status="complete")
        tid = ctx.uploads.create_task_from_upload(job_id, uid, opts)
    except UploadError as e:
        _abandon(ctx, job_id)
        raise ToolFailure("already_converting", "this file is being converted right now", **e.extra) from None
    except BaseException:
        _abandon(ctx, job_id)
        raise
    _publish(ctx, job_id, tid)
    return job_id, tid


def _publish(ctx, job_id: str, tid: str) -> None:
    ctx.store.refresh_job_status(job_id)
    ctx.queue.publish_task(tid)
    ctx.queue.publish_job(job_id)
    ctx.queue.publish_queue()
    ctx.queue.wake()


async def wait_for_task(ctx, task_id: str, seconds: int, report) -> dict:
    deadline = time.monotonic() + seconds
    last = None
    while True:
        task = ctx.store.get_task(task_id)
        st = serialize_task(ctx.store, task, with_segments=False)
        prog = st["progress"]
        cur = (prog["pages_done"], prog["pages_total"], task["status"])
        if cur != last and report is not None:
            await report(float(prog["pages_done"]), float(prog["pages_total"]) if prog["pages_total"] else None, task["status"])
            last = cur
        if task["status"] in _TERMINAL or time.monotonic() >= deadline:
            return task
        await anyio.sleep(0.5)


def _result(ctx, task_id: str, job_id: str) -> ConvertOut:
    task = ctx.store.get_task(task_id)
    doc = ctx.store.find_document(task["sha256"], task["output_dir"]) if task["status"] in ("done", "low") else None
    return ConvertOut(job_id=job_id, status=task["status"], doc_id=doc["id"] if doc else None)


def check_local_path(path: str, roots: list[str]) -> Path:
    deny = ToolFailure("path_not_allowed", "this path cannot be converted from here",
                       hint="only files under the server's configured local roots are accepted; use convert_document instead")
    if not path or "\x00" in path:
        raise deny
    s = path.replace("\\", "/")
    if s.startswith("//") or path.startswith(("\\\\?\\", "\\\\.\\")):
        raise deny
    p = Path(path)
    if not p.is_absolute():
        raise deny
    try:
        real = p.resolve()                                   # follows symlinks and junctions; no network paths get here
    except (OSError, RuntimeError):
        raise deny from None
    real_n = os.path.normcase(str(real))
    inside = False
    for root in roots:
        try:
            r = os.path.normcase(str(Path(root).resolve()))
        except (OSError, RuntimeError):
            continue
        if real_n == r or real_n.startswith(r.rstrip(os.sep) + os.sep):
            inside = True
            break
    if not inside:
        raise deny
    if not real.is_file():
        raise ToolFailure("path_not_found", f"{path} is not an existing file", hint="give the absolute path of a file")
    return real


def register_convert_path_tool(mcp, ctx) -> None:
    @doc4ai_tool(mcp, ctx, name="convert_path", title="Convert a file by server path",
                 description="Convert a file that already sits on the Doc4AI Studio host, by absolute path. Only paths under "
                             "the server's configured local roots are accepted. Returns a job_id (or status \"cached\").",
                 scope=SCOPE_CONVERT_LOCAL, idempotent=True, destructive=False)
    async def convert_path(path: Annotated[str, Field(min_length=1, max_length=1024)],
                           engine: Literal["markitdown", "docling", "mineru"] | None = None,
                           lang: Literal["cht", "en"] | None = None,
                           force: bool = False,
                           wait_seconds: Annotated[int, Field(ge=0, le=30)] = 0,
                           ctx_: Context = None) -> ConvertOut:
        cfg = ctx.config
        principal = current_principal.get()
        real = check_local_path(path, cfg.mcp.local_path_roots)
        opts = build_convert_options(cfg, engine=engine, lang=lang, force=force)
        sha = file_sha256(real)
        out_dir = planned_output_dir(ctx.store, opts.output_dir, real, sha)
        if not force:
            hit = lookup_cached(ctx.store, sha, out_dir)
            if hit is not None and hit.get("id"):
                return ConvertOut(job_id=None, status="cached", doc_id=hit["id"])
        token_id = principal.token_id if principal else None
        _guard_capacity(ctx, token_id, real.stat().st_size)

        def start() -> tuple[str, str]:
            job_id = reserve_job(ctx, opts, token_id)
            try:
                tid, _ = register_source(ctx.store, job_id, real, opts)
            except BaseException:
                _abandon(ctx, job_id)
                raise
            _publish(ctx, job_id, tid)
            return job_id, tid
        job_id, tid = await anyio.to_thread.run_sync(start)
        if wait_seconds > 0:
            await wait_for_task(ctx, tid, wait_seconds, ctx_.report_progress if ctx_ is not None else None)
        return _result(ctx, tid, job_id)


def register_convert_tools(mcp, ctx) -> None:
    @doc4ai_tool(mcp, ctx, name="convert_document", title="Convert a document (upload)",
                 description="Upload a file as base64 and convert it to Markdown. Returns a job_id to poll with get_job; "
                             "identical content already converted returns status \"cached\" with its doc_id. With wait_seconds "
                             "> 0 the call waits (and reports progress) up to that long. Max size: mcp.max_upload_mb.",
                 scope=SCOPE_CONVERT, idempotent=True, destructive=False)
    async def convert_document(filename: Annotated[str, Field(min_length=1, max_length=255)],
                               content_base64: Annotated[str, Field(min_length=4)],
                               engine: Literal["markitdown", "docling", "mineru"] | None = None,
                               lang: Literal["cht", "en"] | None = None,
                               force: bool = False,
                               wait_seconds: Annotated[int, Field(ge=0, le=30)] = 0,
                               ctx_: Context = None) -> ConvertOut:
        cfg = ctx.config
        principal = current_principal.get()
        if Path(filename).suffix.lower() not in ALLOWED_EXTS:   # the extension first: before any decoding
            check_magic(filename, b"")
        data = decode_content(content_base64, cfg.mcp.max_upload_mb * 1024 * 1024)
        check_magic(filename, data)
        opts = build_convert_options(cfg, engine=engine, lang=lang, force=force)
        sha = hashlib.sha256(data).hexdigest()
        out_dir = planned_output_dir(ctx.store, opts.output_dir, Path(display_name(filename)), sha)
        if not force:
            hit = lookup_cached(ctx.store, sha, out_dir)
            if hit is not None and hit.get("id"):
                return ConvertOut(job_id=None, status="cached", doc_id=hit["id"])
        token_id = principal.token_id if principal else None
        _guard_capacity(ctx, token_id, len(data))
        job_id, tid = await anyio.to_thread.run_sync(start_job_for_bytes, ctx, filename, data, opts, token_id)
        if wait_seconds > 0:
            await wait_for_task(ctx, tid, wait_seconds, ctx_.report_progress if ctx_ is not None else None)
        return _result(ctx, tid, job_id)

    register_convert_path_tool(mcp, ctx)
