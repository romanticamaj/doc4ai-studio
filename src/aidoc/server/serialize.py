"""DB rows -> API objects (index §8 object shapes). Used by the routers and by the queue's SSE events."""
from __future__ import annotations

from pathlib import Path

from aidoc.models import TERMINAL_TASK, SegmentStatus

_TERMINAL = {s.value for s in TERMINAL_TASK}
SEGMENT_KEYS = ("id", "idx", "page_start", "page_end", "status", "attempt")


def serialize_segment(seg: dict, task_id: str | None = None) -> dict:
    out = {k: seg.get(k) for k in SEGMENT_KEYS}
    if task_id is not None:
        out["task_id"] = task_id
    return out


def task_progress(segs: list[dict]) -> dict:
    ranged = [s for s in segs if s.get("page_start") is not None and s.get("page_end") is not None]
    if ranged:
        total = sum(s["page_end"] - s["page_start"] + 1 for s in ranged)
        done = sum(s["page_end"] - s["page_start"] + 1 for s in ranged if s["status"] == SegmentStatus.done.value)
    else:
        total = len(segs)
        done = sum(1 for s in segs if s["status"] == SegmentStatus.done.value)
    return {"pages_done": done, "pages_total": total}


def serialize_task(store, task: dict, with_segments: bool = True) -> dict:
    segs = store.list_segments(task["id"])
    out = {k: v for k, v in task.items() if k != "pid"}
    doc = store.find_document(task["sha256"], task["output_dir"]) if task.get("output_dir") else None
    out["document_id"] = doc["id"] if doc else None
    out["pages"] = doc["pages"] if doc else None
    out["progress"] = task_progress(segs)
    if with_segments:
        out["segments"] = [serialize_segment(s) for s in segs]
    return out


def serialize_job(store, job: dict) -> dict:
    tasks = store.list_tasks(job["id"])
    out = dict(job)
    out["progress"] = {"done": sum(1 for t in tasks if t["status"] in _TERMINAL), "total": len(tasks)}
    return out


def serialize_document(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k not in ("work_copy_path", "work_copy_expires_at")}
    out["stem"] = Path(doc["output_dir"]).name
    return out
