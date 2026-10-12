"""Conversions behind the compat endpoints (compat spec §4.3, §4.5; plan D3–D6, spike S4).

One pipeline: jobs are reserved with the token's cap (`tools_convert.reserve_job_once`, origin "compat"), the file
goes through the upload area (`tools_convert.start_job_for_file`) and the shared JobQueue converts it. A request for
content that is already converted answers from the library (cache, by sha256 regardless of the file name); content
that is being converted right now is attached to instead of converted twice."""
from __future__ import annotations

import hashlib
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import anyio
from fastapi.responses import JSONResponse
from python_multipart.multipart import MultipartParser, parse_options_header

from aidoc.compat.auth import CompatCall, CompatError
from aidoc.compat.options import CompatOptions
from aidoc.compat.render import build_response, failure_response
from aidoc.mcp.budget import estimate_tokens
from aidoc.mcp.errors import ToolFailure
from aidoc.mcp.tools_convert import (
    _TERMINAL,
    build_convert_options,
    check_magic,
    read_head,
    reserve_job_once,
    start_job_for_file,
    wait_for_task,
)
from aidoc.output import lookup_cached, planned_output_dir
from aidoc.server.serialize import serialize_task
from aidoc.server.uploads import display_name

MIB = 1024 * 1024
FIELD_MAX_BYTES = 64 * 1024
FIELDS_MAX = 200
SLOT_RETRY_S = 0.5
VISIBLE = ("ok", "warn", "low")
_UUID_PREFIX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}_(?=.)")
_admit_lock = threading.Lock()          # cache / running check + job creation happen as one step (plan review 4)


@dataclass
class Upload:
    filename: str
    part_path: Path
    size: int
    sha256: str
    fields: dict = field(default_factory=dict)


def clean_name(filename: str) -> str:
    """The client's file name without directories and without the `<uuid>_` prefix Open WebUI puts on its stored
    uploads (its loader sends the stored path, spike S5)."""
    return _UUID_PREFIX.sub("", display_name(filename or "")) or "document"


def new_part_path(ctx) -> Path:
    d = ctx.uploads.dir
    d.mkdir(parents=True, exist_ok=True)
    return d / f"compat-{uuid.uuid4().hex}.part"


def too_large(call: CompatCall, limit_mb: int) -> CompatError:
    call.fail("file_too_large")
    return CompatError(413, f"file exceeds {limit_mb} MB")


async def stream_upload(request, call: CompatCall, *, max_mb: int) -> Upload:
    """The single `files` part of a multipart body, streamed to a part file with a byte cap (spike S4: Starlette's
    form() would spool an unbounded file first). Other parts are option fields. Raises CompatError 413/422 and never
    leaves a part file behind."""
    ctx = request.app.state.ctx
    ctype, params = parse_options_header(request.headers.get("content-type"))
    if ctype != b"multipart/form-data" or not params.get(b"boundary"):
        raise CompatError(422, "send multipart/form-data with the document in the field 'files'")
    max_bytes = max_mb * MIB
    st = {"headers": {}, "hname": b"", "hval": b"", "kind": None, "name": None, "filename": None,
          "value": bytearray(), "file": None, "path": None, "size": 0, "files": 0, "error": None}
    fields: dict[str, list[str]] = {}
    sha = hashlib.sha256()

    def fail(err: CompatError) -> None:
        if st["error"] is None:
            st["error"] = err

    def on_part_begin():
        st["headers"], st["kind"] = {}, None

    def on_header_field(data, start, end):
        st["hname"] += data[start:end]

    def on_header_value(data, start, end):
        st["hval"] += data[start:end]

    def on_header_end():
        st["headers"][st["hname"].decode("latin-1").lower()] = st["hval"]
        st["hname"], st["hval"] = b"", b""

    def on_headers_finished():
        _, disp = parse_options_header(st["headers"].get("content-disposition", b""))
        name = disp.get(b"name", b"").decode("utf-8", "replace")
        fname = disp.get(b"filename")
        if fname is not None:
            st["files"] += 1
            if st["files"] > 1 or name != "files":
                fail(CompatError(422, "one file per request" if name == "files" else f"unexpected file field {name!r}"))
                st["kind"] = "skip"
                return
            try:
                st["filename"] = fname.decode("utf-8")
            except UnicodeDecodeError:
                st["filename"] = fname.decode("latin-1")
            st["path"] = new_part_path(ctx)
            st["file"] = open(st["path"], "wb")  # noqa: SIM115  closed in on_part_end / cleanup
            st["kind"] = "file"
        else:
            if sum(len(v) for v in fields.values()) >= FIELDS_MAX:
                fail(CompatError(422, f"more than {FIELDS_MAX} form fields"))
                st["kind"] = "skip"
                return
            st["kind"], st["name"], st["value"] = "field", name, bytearray()

    def on_part_data(data, start, end):
        if st["error"] is not None or st["kind"] == "skip":
            return
        chunk = data[start:end]
        if st["kind"] == "file":
            st["size"] += len(chunk)
            if st["size"] > max_bytes:
                fail(too_large(call, max_mb))
                return
            st["file"].write(chunk)
            sha.update(chunk)
        elif st["kind"] == "field":
            st["value"] += chunk
            if len(st["value"]) > FIELD_MAX_BYTES:
                fail(CompatError(422, f"form field {st['name']!r} is larger than {FIELD_MAX_BYTES} bytes"))

    def on_part_end():
        if st["kind"] == "file" and st["file"] is not None:
            st["file"].close()
            st["file"] = None
        elif st["kind"] == "field" and st["error"] is None:
            fields.setdefault(st["name"], []).append(bytes(st["value"]).decode("utf-8", "replace"))
        st["kind"] = None

    parser = MultipartParser(params[b"boundary"], {
        "on_part_begin": on_part_begin, "on_header_field": on_header_field, "on_header_value": on_header_value,
        "on_header_end": on_header_end, "on_headers_finished": on_headers_finished, "on_part_data": on_part_data,
        "on_part_end": on_part_end, "on_end": lambda: st.__setitem__("ended", True)})
    try:
        async for chunk in request.stream():
            parser.write(chunk)
            if st["error"] is not None:
                break
        else:
            parser.finalize()
        if st["error"] is not None:
            raise st["error"]
        if st["path"] is None:
            raise CompatError(422, "no file: send the document in the multipart field 'files'")
        if st["file"] is not None or not st.get("ended"):
            # no closing boundary: the upload was cut off; never convert (and cache) a truncated file (review 4)
            raise CompatError(422, "incomplete multipart body: the closing boundary is missing")
    except BaseException as e:
        if st["file"] is not None:
            st["file"].close()
        if st["path"] is not None:
            st["path"].unlink(missing_ok=True)
        if isinstance(e, CompatError):
            raise
        if isinstance(e, Exception) and not isinstance(e, (anyio.get_cancelled_exc_class(),)):
            raise CompatError(422, f"malformed multipart body: {type(e).__name__}") from None
        raise
    flat = {k: (v[0] if len(v) == 1 else v) for k, v in fields.items()}
    return Upload(filename=st["filename"], part_path=st["path"], size=st["size"], sha256=sha.hexdigest(), fields=flat)


def write_part(ctx, data: bytes, filename: str) -> Upload:
    path = new_part_path(ctx)
    path.write_bytes(data)
    return Upload(filename=filename, part_path=path, size=len(data), sha256=hashlib.sha256(data).hexdigest())


# ---- status

def task_to_docling_status(task: dict) -> str:
    """D6 (as amended in Task 11): queued → pending; probing|converting|checking → started; done|low|skipped →
    success; failed|cancelled → failure. A low-quality conversion is a finished *task* — docling-serve's task states
    are pending|started|success|failure and clients (Haystack) stop polling only on success|failure; the result's
    conversion status says partial_success."""
    s = task["status"]
    if s == "queued":
        return "pending"
    if s in ("probing", "converting", "checking"):
        return "started"
    if s in ("done", "low", "skipped"):
        return "success"
    return "failure"


def task_message(task: dict) -> str:
    if task["status"] == "cancelled":
        return f"cancelled: {task.get('error_msg') or 'the conversion was cancelled'}"
    return f"{task.get('error_kind') or 'engine'}: {task.get('error_msg') or 'conversion failed'}"


def task_meta(ctx, task: dict) -> dict:
    """docling's TaskProcessingMeta (spike S2) plus our page progress."""
    prog = serialize_task(ctx.store, task, with_segments=False)["progress"]
    state = task_to_docling_status(task)
    low = task["status"] == "low"
    return {"num_docs": 1, "num_processed": int(state in ("success", "failure")),
            "num_succeeded": int(state == "success" and not low), "num_partially_succeeded": int(low),
            "num_failed": int(state == "failure"),
            "pages_done": prog["pages_done"], "pages_total": prog["pages_total"]}


def task_position(ctx, task: dict) -> int | None:
    if task["status"] != "queued":
        return None
    r = ctx.store._q1("SELECT COUNT(*) FROM tasks WHERE status='queued' AND created_at < ?", (task["created_at"],))
    return int(r[0]) if r else 0


# ---- admission

@dataclass
class Admission:
    kind: str                       # cached | attached | started
    doc: dict | None = None
    task_id: str | None = None
    job_id: str | None = None


def find_cached(ctx, sha: str, out_dir: Path) -> dict | None:
    """A converted document with this content: the planned output dir first, then any other (spec §4.3 step 2 —
    a file converted in the Library is a hit under any name)."""
    hit = lookup_cached(ctx.store, sha, out_dir)
    if hit is not None and hit.get("id"):
        return ctx.store.get_document(hit["id"]) or hit
    for doc in ctx.store.documents_by_sha(sha):
        if doc["status"] in VISIBLE and lookup_cached(ctx.store, sha, Path(doc["output_dir"])) is not None:
            return doc
    return None


def admit(ctx, call: CompatCall, upload: Upload, name: str, convert, opts: CompatOptions) -> Admission | None:
    """Cached / attached / started, or None when the token has no free job slot. One locked step, so two requests
    for the same file never start two jobs (review focus 4)."""
    with _admit_lock:
        out_dir = planned_output_dir(ctx.store, convert.output_dir, Path(name), upload.sha256)
        if not opts.force:
            doc = find_cached(ctx, upload.sha256, out_dir)
            if doc is not None:
                return Admission("cached", doc=doc)
        live = ctx.store.live_task_for_sha(upload.sha256)
        if live is not None:
            return Admission("attached", task_id=live["id"], job_id=live["job_id"])
        token_id = call.principal.token_id if call.principal else None
        job_id = reserve_job_once(ctx, convert, token_id, origin="compat",
                                  limit=ctx.config.compat.max_concurrent_jobs_per_token)
        if job_id is None:
            return None
        try:
            job_id, task_id = start_job_for_file(ctx, name, upload.part_path, upload.sha256, upload.size, convert,
                                                 token_id, origin="compat", job_id=job_id)
        except ToolFailure as f:                        # another process converts this file: wait on its task
            if f.code == "already_converting" and f.extra.get("task_id"):
                return Admission("attached", task_id=f.extra["task_id"], job_id=f.extra.get("job_id"))
            raise
        ctx.store.create_compat_task(job_id, token_id=token_id or "", options={**opts.to_json(), "_filename": name},
                                     protocol=call.protocol)
        return Admission("started", task_id=task_id, job_id=job_id)


def _disk_problem(ctx, size: int) -> str | None:
    up = ctx.uploads.dir
    up.mkdir(parents=True, exist_ok=True)
    needed = size * ctx.config.limits.disk_space_factor
    free = shutil.disk_usage(up).free
    return None if free >= needed else f"insufficient_disk: {needed} bytes needed, {free} free"


def summarize(call: CompatCall, upload: Upload, name: str, opts: CompatOptions) -> None:
    call.args = {"filename": name, "size": upload.size, "sha256": upload.sha256[:8], "to_formats": opts.to_formats,
                 "placeholder": bool(opts.placeholder), "lang": opts.lang, "engine": opts.engine}


def precheck(ctx, call: CompatCall, upload: Upload, name: str):
    """File type (A-M10) and disk space; a failure response (HTTP 200, status failure) or None."""
    try:
        check_magic(name, read_head(upload.part_path))
    except ToolFailure as f:
        upload.part_path.unlink(missing_ok=True)
        call.fail(f.code)
        return failure_response(name, None, f"{f.code}: {f.message}", call.ts)
    problem = _disk_problem(ctx, upload.size)
    if problem:
        upload.part_path.unlink(missing_ok=True)
        call.fail("insufficient_disk")
        return failure_response(name, None, problem, call.ts)
    return None


def respond(model, call: CompatCall | None = None, md: str | None = None) -> JSONResponse:
    if call is not None and md:
        call.response_tokens_est = estimate_tokens(md)[0]
    return JSONResponse(model.model_dump(mode="json"), headers={"Cache-Control": "no-store"})


def response_for_task(ctx, request, call: CompatCall, task: dict, opts: CompatOptions, *, attached: bool, name: str):
    if task["status"] in ("done", "low", "skipped"):
        doc = ctx.store.find_document(task["sha256"], task["output_dir"])
        if doc is None or doc["status"] not in VISIBLE:
            doc = find_cached(ctx, task["sha256"], Path(task["output_dir"]))
        if doc is not None:
            model = build_response(ctx, request, doc, task, opts, cached=task["status"] == "skipped", attached=attached,
                                   started=call.ts, filename=name)
            if model.status == "failure":
                call.fail("output_missing")
            return respond(model, call, model.document.md_content)
        call.fail("output_missing")
        return respond(failure_response(name, task, "output_missing: the converted output is not on disk", call.ts))
    call.fail("conversion_failed")
    return respond(failure_response(name, task, task_message(task), call.ts))


def _timeout_body(job_id: str | None, detail: str) -> dict:
    return {"detail": detail, "task_id": job_id,
            "status_url": f"/v1/status/poll/{job_id}" if job_id else None,
            "result_url": f"/v1/result/{job_id}" if job_id else None}


def attach_key(ctx, call: CompatCall, adm: Admission, opts: CompatOptions, name: str) -> str:
    """A task id this token may poll for an attached conversion: the job id when it is this token's own compat
    task, else a new opaque id that resolves to the running task (D5)."""
    token_id = call.principal.token_id if call.principal else ""
    own = ctx.store.get_compat_task(adm.job_id) if adm.job_id else None
    if own is not None and own["token_id"] == token_id:
        return adm.job_id
    key = "t" + uuid.uuid4().hex
    ctx.store.create_compat_task(key, token_id=token_id, options={**opts.to_json(), "_filename": name,
                                                                  "_task_id": adm.task_id}, protocol=call.protocol)
    return key


def note_job(call: CompatCall, job_id: str | None, *, own: bool) -> None:
    """mcp_calls.job_id counts against the token's job cap (Store.active_mcp_jobs): only the token's own job goes
    there; a job it merely waits on (attached: another token's, a Library batch) is named in args (review finding 3)."""
    if not job_id:
        return
    if own:
        call.job_id = job_id
    else:
        call.args = {**(call.args or {}), "attached_job_id": job_id}


async def run_sync(ctx, request, call: CompatCall, upload: Upload, opts: CompatOptions):
    """Spec §4.3: wait for a job slot, then for the conversion, up to compat.sync_timeout_s (or document_timeout)."""
    name = clean_name(upload.filename)
    summarize(call, upload, name, opts)
    failed = precheck(ctx, call, upload, name)
    if failed is not None:
        return respond(failed)
    cfg = ctx.config
    convert = build_convert_options(cfg, engine=opts.engine, lang=opts.lang, force=opts.force)
    deadline = time.monotonic() + min(opts.timeout_s or cfg.compat.sync_timeout_s, cfg.compat.sync_timeout_s)
    while True:
        try:
            adm = await anyio.to_thread.run_sync(admit, ctx, call, upload, name, convert, opts)
        except ToolFailure as f:
            upload.part_path.unlink(missing_ok=True)
            call.fail(f.code)
            return respond(failure_response(name, None, f"{f.code}: {f.message}", call.ts))
        except BaseException:
            upload.part_path.unlink(missing_ok=True)
            raise
        if adm is not None or time.monotonic() >= deadline:
            break
        await anyio.sleep(SLOT_RETRY_S)                  # D3: queue behind the token's own jobs, do not refuse
    if adm is None:
        upload.part_path.unlink(missing_ok=True)
        call.fail("sync_timeout")
        return JSONResponse(_timeout_body(None, "no job slot became free"), status_code=504)
    if adm.kind != "started":
        upload.part_path.unlink(missing_ok=True)
    if adm.kind == "cached":
        model = build_response(ctx, request, adm.doc, None, opts, cached=True, attached=False, started=call.ts,
                               filename=name)
        if model.status == "failure":
            call.fail("output_missing")
        return respond(model, call, model.document.md_content)
    note_job(call, adm.job_id, own=adm.kind == "started")
    task = await wait_for_task(ctx, adm.task_id, max(0, int(deadline - time.monotonic() + 0.999)), report=None)
    if task["status"] not in _TERMINAL:
        call.fail("sync_timeout")
        key = adm.job_id if adm.kind == "started" else attach_key(ctx, call, adm, opts, name)
        return JSONResponse(_timeout_body(key, "conversion still running"), status_code=504)
    return response_for_task(ctx, request, call, task, opts, attached=adm.kind == "attached", name=name)


# ---- async (spec §4.5)

def _status_model(ctx, key: str, *, task: dict | None = None, state: str | None = None, message: str | None = None):
    from aidoc.compat.render import TaskStatusResponse, failure_info
    if task is not None:
        state = task_to_docling_status(task)
        if state == "failure":
            message = task_message(task)
        meta = task_meta(ctx, task)
        position = task_position(ctx, task) if task["status"] in ("queued",) else (
            None if task["status"] in _TERMINAL else 0)
    else:
        meta = {"num_docs": 1, "num_processed": 1, "num_succeeded": int(state == "success"),
                "num_partially_succeeded": int(state == "partial_success"), "num_failed": int(state == "failure")}
        position = None
    return TaskStatusResponse(task_id=key, task_status=state, task_position=position, task_meta=meta,
                              error_message=message if state == "failure" else None,
                              failure=failure_info(message, task) if state == "failure" else None)


def _synthetic(ctx, call: CompatCall, opts: CompatOptions, name: str, **extra) -> str:
    key = "t" + uuid.uuid4().hex
    ctx.store.create_compat_task(key, token_id=call.principal.token_id or "",
                                 options={**opts.to_json(), "_filename": name, **extra}, protocol=call.protocol)
    return key


async def submit_async(ctx, request, call: CompatCall, upload: Upload, opts: CompatOptions):
    """The sync prelude once, then an immediate TaskStatusResponse (task_id = job_id, D5). A full token gets 429."""
    name = clean_name(upload.filename)
    summarize(call, upload, name, opts)
    failed = precheck(ctx, call, upload, name)
    if failed is not None:
        message = failed.errors[0].error_message
        key = _synthetic(ctx, call, opts, name, _failure=message)
        return respond(_status_model(ctx, key, state="failure", message=message))
    convert = build_convert_options(ctx.config, engine=opts.engine, lang=opts.lang, force=opts.force)
    try:
        adm = await anyio.to_thread.run_sync(admit, ctx, call, upload, name, convert, opts)
    except ToolFailure as f:
        upload.part_path.unlink(missing_ok=True)
        call.fail(f.code)
        message = f"{f.code}: {f.message}"
        return respond(_status_model(ctx, _synthetic(ctx, call, opts, name, _failure=message), state="failure",
                                     message=message))
    except BaseException:
        upload.part_path.unlink(missing_ok=True)
        raise
    if adm is None:
        upload.part_path.unlink(missing_ok=True)
        call.fail("too_many_jobs", "rate_limited")
        raise CompatError(429, "too many jobs in progress", headers={"Retry-After": "5"}, retry_after=5)
    if adm.kind != "started":
        upload.part_path.unlink(missing_ok=True)
    if adm.kind == "cached":
        key = _synthetic(ctx, call, opts, name, _cached_doc_id=adm.doc["id"])
        return respond(_status_model(ctx, key, state="success"))
    note_job(call, adm.job_id, own=adm.kind == "started")
    key = adm.job_id if adm.kind == "started" else attach_key(ctx, call, adm, opts, name)
    return respond(_status_model(ctx, key, task=ctx.store.get_task(adm.task_id)))


@dataclass
class Resolved:
    key: str
    opts: CompatOptions
    name: str
    task: dict | None = None
    doc: dict | None = None
    failure: str | None = None

    @property
    def terminal(self) -> bool:
        return self.task is None or self.task["status"] in _TERMINAL


def resolve(ctx, key: str, principal) -> Resolved:
    """A task id this token created (D5); anything else — unknown, pruned, another token's — is 404."""
    row = ctx.store.get_compat_task(key) if key else None
    if row is None or row["token_id"] != (principal.token_id or ""):
        raise CompatError(404, "task not found")
    o = row["options"] or {}
    res = Resolved(key=key, opts=CompatOptions.from_json(o), name=o.get("_filename") or "document")
    if o.get("_failure"):
        res.failure = o["_failure"]
    elif o.get("_cached_doc_id"):
        res.doc = ctx.store.get_document(o["_cached_doc_id"])
        if res.doc is None:
            res.failure = "output_missing: the document was deleted"
    else:
        tid = o.get("_task_id")
        res.task = ctx.store.get_task(tid) if tid else next(iter(ctx.store.list_tasks(job_id=key)), None)
        if res.task is None:
            res.failure = "cancelled: the conversion no longer exists"
    return res


def status_of(ctx, res: Resolved):
    if res.failure:
        return _status_model(ctx, res.key, state="failure", message=res.failure)
    if res.doc is not None:
        return _status_model(ctx, res.key, state="success")
    return _status_model(ctx, res.key, task=res.task)


async def poll(ctx, call: CompatCall, key: str, wait: float):
    deadline = time.monotonic() + max(0.0, min(wait, ctx.config.compat.poll_max_wait_s))
    while True:
        res = resolve(ctx, key, call.principal)
        note_job(call, res.task["job_id"] if res.task else None, own=res.task is not None and res.task["job_id"] == key)
        if res.terminal or time.monotonic() >= deadline:
            return respond(status_of(ctx, res))
        await anyio.sleep(min(0.5, max(0.05, deadline - time.monotonic())))


def result(ctx, request, call: CompatCall, key: str):
    from aidoc.compat.render import TaskFailureResult, failure_info
    res = resolve(ctx, key, call.principal)
    note_job(call, res.task["job_id"] if res.task else None, own=res.task is not None and res.task["job_id"] == key)
    if not res.terminal:
        raise CompatError(404, "task not finished")
    if res.failure or (res.task is not None and res.task["status"] in ("failed", "cancelled")):
        message = res.failure or task_message(res.task)
        call.fail("conversion_failed")
        return respond(TaskFailureResult(failure=failure_info(message, res.task)))
    if res.doc is not None:
        model = build_response(ctx, request, res.doc, None, res.opts, cached=True, attached=False, started=0.0,
                               filename=res.name)
        return respond(model, call, model.document.md_content)
    return response_for_task(ctx, request, call, res.task, res.opts, attached=False, name=res.name)
