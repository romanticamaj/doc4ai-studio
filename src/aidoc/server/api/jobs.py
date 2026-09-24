"""Jobs / tasks / queue endpoints (index §8)."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, model_validator

from aidoc.batch import collect_inputs, register_source
from aidoc.models import ConvertOptions
from aidoc.output import planned_output_dir
from aidoc.server.auth import ApiError, allow_local_paths
from aidoc.server.jobs import RetryError
from aidoc.server.serialize import serialize_job, serialize_task
from aidoc.server.uploads import UploadError

router = APIRouter()


class InputRef(BaseModel):
    upload_id: str | None = None
    path: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.upload_id is None) == (self.path is None):
            raise ValueError("each input needs exactly one of upload_id or path")
        return self


class JobIn(BaseModel):
    inputs: list[InputRef] = Field(min_length=1)
    engine: Literal["markitdown", "docling", "mineru"] | None = None
    lang: Literal["cht", "en"] | None = None
    force: bool = False
    retry_low: bool = False
    allow_online_audio: bool = False
    origin: Literal["web", "cli"] = "web"
    output_dir: str | None = None          # CLI forwarding (-o); local paths rule applies
    timeout_s: int | None = Field(default=None, ge=1)     # CLI --timeout


class RetryIn(BaseModel):
    use_new_version: bool = False
    retry_low: bool = False


def _ctx(request: Request):
    return request.app.state.ctx


def build_options(cfg, body: JobIn) -> ConvertOptions:
    out = Path(body.output_dir) if body.output_dir else cfg.output_root()
    return ConvertOptions(output_dir=out, engine=body.engine, lang=body.lang or cfg.general.lang, force=body.force,
                          retry_low=body.retry_low, timeout_s=body.timeout_s,
                          allow_online_audio=bool(body.allow_online_audio or cfg.general.enable_audio),
                          mineru_tier=cfg.engines.mineru_tier, docling_ocr=cfg.engines.docling_ocr)


@router.post("/jobs", status_code=201)
def create_job(body: JobIn, request: Request) -> dict:
    ctx = _ctx(request)
    store = ctx.store
    paths = [Path(i.path) for i in body.inputs if i.path is not None]
    seen: set[str] = set()
    for i in body.inputs:                      # an upload is consumed by its task: it can back only one input
        if i.upload_id is not None:
            if i.upload_id in seen:
                raise ApiError(400, "duplicate_input", upload_id=i.upload_id)
            seen.add(i.upload_id)
    if (paths or body.output_dir) and not allow_local_paths(request, ctx):
        raise ApiError(403, "local_path_forbidden")
    opts = build_options(ctx.config, body)
    files_of: dict[int, list[Path]] = {}            # input index -> the files it stands for
    for n, ref in enumerate(body.inputs):
        if ref.path is None:
            continue
        p = Path(ref.path)
        if not p.is_absolute():                     # a relative path would depend on the server's cwd
            raise ApiError(400, "path_not_absolute", path=str(p))
        if p.is_dir():                              # spec §6 page 1: a local folder (recursive, like aidoc batch)
            files_of[n] = collect_inputs(p, exclude=opts.output_dir)
            if not files_of[n]:
                raise ApiError(400, "no_inputs", path=str(p))
        elif p.is_file():
            files_of[n] = [p]
        else:
            raise ApiError(400, "input_not_found", path=str(p))
    sizes = [f.stat().st_size for fs in files_of.values() for f in fs]
    uploads = [store.get_upload(i.upload_id) for i in body.inputs if i.upload_id is not None]
    for i, up in zip([i for i in body.inputs if i.upload_id is not None], uploads):
        if up is None:
            raise ApiError(404, "upload_not_found", upload_id=i.upload_id)
        if up["status"] != "complete":
            raise ApiError(409, "upload_incomplete", upload_id=up["id"], received=up["received"], size=up["size"])
        sizes.append(up["size"])
    # all-or-nothing (final review I1): refuse before anything is created or any upload is consumed
    for up in uploads:
        out_dir = planned_output_dir(store, opts.output_dir, Path(up["filename"]), up["sha256"])
        busy = store.busy_task_id(up["sha256"], out_dir)
        if busy is not None:
            raise ApiError(409, "already_converting", task_id=busy, upload_id=up["id"])
    needed = sum(sizes) * ctx.config.limits.disk_space_factor
    opts.output_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(opts.output_dir).free
    if free < needed:
        raise ApiError(507, "insufficient_disk", needed=needed, free=free)
    job_id = store.create_job(opts, body.origin)
    task_ids: list[str] = []
    for n, ref in enumerate(body.inputs):
        if ref.path is not None:
            for f in files_of[n]:
                tid, _ = register_source(store, job_id, f.resolve(), opts)
                if tid not in task_ids:
                    task_ids.append(tid)
            continue
        try:
            tid = ctx.uploads.create_task_from_upload(job_id, ref.upload_id, opts)
        except UploadError as e:
            raise ApiError(e.status, e.error, **e.extra) from None
        if tid not in task_ids:
            task_ids.append(tid)
    store.refresh_job_status(job_id)
    for tid in task_ids:
        ctx.queue.publish_task(tid)
    ctx.queue.publish_job(job_id)
    ctx.queue.publish_queue()
    ctx.queue.wake()
    return {"job": serialize_job(store, store.get_job(job_id))}


@router.get("/jobs")
def list_jobs(request: Request, limit: int = 100) -> dict:
    store = _ctx(request).store
    return {"jobs": [serialize_job(store, j) for j in store.list_jobs(limit=max(1, min(limit, 1000)))]}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict:
    store = _ctx(request).store
    job = store.get_job(job_id)
    if job is None:
        raise ApiError(404, "not_found")
    return {"job": serialize_job(store, job), "tasks": [serialize_task(store, t) for t in store.list_tasks(job_id)]}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict:
    ctx = _ctx(request)
    if ctx.store.get_job(job_id) is None:
        raise ApiError(404, "not_found")
    ctx.queue.cancel_job(job_id)
    return {"job": serialize_job(ctx.store, ctx.store.get_job(job_id))}


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: str, request: Request, body: RetryIn | None = None) -> dict:
    ctx = _ctx(request)
    body = body or RetryIn()
    try:
        task = ctx.queue.retry_task(task_id, use_new_version=body.use_new_version, retry_low=body.retry_low)
    except RetryError as e:
        raise ApiError(e.status, e.error, **e.extra) from None
    return {"task": serialize_task(ctx.store, task)}


@router.post("/queue/pause")
def pause(request: Request) -> dict:
    q = _ctx(request).queue
    q.pause()
    return {"queue": q.snapshot()}


@router.post("/queue/resume")
def resume(request: Request) -> dict:
    q = _ctx(request).queue
    q.resume()
    return {"queue": q.snapshot()}
