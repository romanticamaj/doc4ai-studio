"""Source copies over the API (spec 2026-10-29 §10.1): summary, backfill (with dry run), retry of one document."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from aidoc import fsops, source_copy
from aidoc.server.auth import ApiError

router = APIRouter()


class BackfillIn(BaseModel):
    dry_run: bool = False


def _publisher(ctx):
    def emit(kind: str, payload: dict) -> None:
        ctx.bus.publish(kind, payload.get("document_id"), payload)
    return emit


@router.get("/sources")
def get_sources(request: Request) -> dict:
    ctx = request.app.state.ctx
    out = source_copy.summary(ctx.store, ctx.config)
    # what 「清除 orphaned 紀錄」 would delete as well (the Library's confirmation dialog, spec §11.3)
    orphans = [d for d in ctx.store.list_documents(status="orphaned") if d.get("source_copy_path")]
    present = [d for d in orphans if source_copy.is_present(d["source_copy_path"])]
    # bytes = what deleting them frees: a copy hardlinked with a document that stays frees nothing ("shared")
    inodes: dict[tuple[int, int], list] = {}
    for d in present:
        try:
            st = os.stat(fsops.long_path(Path(d["source_copy_path"])))
        except OSError:
            continue
        inodes.setdefault((st.st_dev, st.st_ino), []).append(st)
    freed = sum(group[0].st_size for group in inodes.values() if group[0].st_nlink <= len(group))
    shared = sum(len(group) for group in inodes.values() if group[0].st_nlink > len(group))
    out["orphaned_copies"] = {"count": len(present), "bytes": freed, "shared": shared}
    return out


@router.post("/sources/backfill")
async def post_backfill(request: Request, body: BackfillIn | None = None) -> dict:
    ctx = request.app.state.ctx
    body = body or BackfillIn()

    def progress(payload: dict) -> None:
        ctx.bus.publish("sources.backfill", None, payload)

    def run() -> dict:
        return source_copy.backfill(ctx.store, ctx.config, dry_run=body.dry_run, progress=progress,
                                    emit=_publisher(ctx))
    try:
        return await run_in_threadpool(run)
    except source_copy.BackfillRunning:
        raise ApiError(409, "backfill_running") from None
    except source_copy.InsufficientDisk as e:
        raise ApiError(507, "insufficient_disk", needed=e.needed, free=e.free) from None


def copy_view(doc: dict) -> dict:
    """The structured `source_copy` of GET /documents/{id} (the flat columns stay private)."""
    path = doc.get("source_copy_path")
    present = bool(path) and source_copy.is_present(path)
    return {"status": doc.get("source_copy_status"), "error": doc.get("source_copy_error"), "present": present,
            "path": path, "size": doc.get("source_copy_size"), "copied_at": doc.get("source_copy_at")}


@router.post("/documents/{doc_id}/source-copy")
async def retry_source_copy(doc_id: str, request: Request) -> dict:
    """「重試備份」: one document through the backfill rules (spec §7.2), written for real."""
    ctx = request.app.state.ctx
    store = ctx.store
    doc = store.get_document(doc_id)
    if doc is None:
        raise ApiError(404, "not_found")
    busy = store.busy_task_id(doc["sha256"], doc["output_dir"])
    if busy is None:
        live = [t for t in store.list_tasks(status_in=["queued", "probing", "converting", "checking"])
                if t["sha256"] == doc["sha256"] and t["output_dir"] == doc["output_dir"]]
        busy = live[0]["id"] if live else None
    if busy is not None:
        raise ApiError(409, "already_converting", task_id=busy)
    row = await run_in_threadpool(source_copy.ensure_copy, store, ctx.config, doc, dry_run=False,
                                  emit=_publisher(ctx))
    view = copy_view(store.get_document(doc_id))
    outcome, detail = row["outcome"], row.get("detail") or ""
    if outcome in ("copied", "already_present"):
        return {"outcome": outcome, "source_copy": view}
    if outcome == "missing_original":
        raise ApiError(410, "source_missing", source_copy=view)
    if outcome == "sha_mismatch":
        raise ApiError(409, "sha_mismatch", detail=detail, source_copy=view)
    if outcome == "skipped_orphaned":
        raise ApiError(409, "orphaned")
    if detail.startswith("no_space"):
        raise ApiError(507, "insufficient_disk", detail=detail, source_copy=view)
    raise ApiError(500, "copy_failed", outcome=outcome, detail=detail, source_copy=view)
