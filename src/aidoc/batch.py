"""Batch conversion (spec §5): in-process queue (index A10), cache via DB, atomic manifest."""
from __future__ import annotations
import json
import threading
import traceback
from pathlib import Path
from typing import Callable

from aidoc import fsops
from aidoc.config import AidocConfig
from aidoc.models import ConvertOptions, ErrorKind, TaskStatus
from aidoc.names import file_sha256
from aidoc.pipeline import run_task
from aidoc.store import Store

IGNORED_NAMES = {"_manifest.jsonl", "chunks.jsonl"}


def collect_inputs(input_dir: Path, exclude: Path | None = None) -> list[Path]:
    """Recursive, sorted by relative path; skips hidden files/dirs, manifests and anything under `exclude`."""
    input_dir = Path(input_dir).resolve()
    exclude = Path(exclude).resolve() if exclude else None
    out = []
    for p in input_dir.rglob("*"):
        rel = p.relative_to(input_dir)
        if any(part.startswith(".") for part in rel.parts) or p.name in IGNORED_NAMES or not p.is_file():
            continue
        if exclude is not None and (p == exclude or exclude in p.parents):
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.relative_to(input_dir).as_posix())


def _source_label(source_path: str, input_root: Path | None) -> str:
    p = Path(source_path)
    if input_root is not None:
        try:
            return p.resolve().relative_to(Path(input_root).resolve()).as_posix()
        except ValueError:
            pass
    return p.name


def manifest_rows(store: Store, job_id: str, input_root: Path | None = None) -> list[dict]:
    rows = []
    for t in store.list_tasks(job_id):
        q = t.get("quality") or {}
        row = {"source": _source_label(t["source_path"], input_root), "status": t["status"], "engine": t["engine"],
               "score": q.get("score"), "level": q.get("level")}
        if t["status"] == TaskStatus.failed.value:
            row["error"] = f"{t['error_kind']}: {t['error_msg']}"
        rows.append(row)
    return rows


def write_manifest(store: Store, job_id: str, out_root: Path, input_root: Path | None = None) -> None:
    fsops.atomic_write_lines(Path(out_root) / "_manifest.jsonl",
                             [json.dumps(r, ensure_ascii=False) for r in manifest_rows(store, job_id, input_root)])


def run_batch(store: Store, config: AidocConfig, engines: dict, inputs: list[Path], opts: ConvertOptions,
              emit: Callable[[str, dict], None] | None = None, cancel: threading.Event | None = None,
              input_root: Path | None = None) -> str:
    job = store.create_job(opts, "cli")
    task_ids = []
    for src in inputs:                                  # create every task first (resume key: sha256, output_dir)
        src = Path(src).resolve()
        st = src.stat()
        tid, _ = store.create_task(job, str(src), file_sha256(src), st.st_size, st.st_mtime, opts.lang,
                                   str(Path(opts.output_dir) / src.stem))
        task_ids.append(tid)
    store.refresh_job_status(job)
    write_manifest(store, job, opts.output_dir, input_root)
    for tid in task_ids:
        if cancel is not None and cancel.is_set():
            store.update_task(tid, status=TaskStatus.cancelled)
            continue
        try:
            run_task(store, tid, engines, config, emit=emit, cancel=cancel)
        except Exception as e:  # noqa: BLE001  a crashing task must never stop the batch
            store.update_task(tid, status=TaskStatus.failed, error_kind=ErrorKind.engine,
                              error_msg=f"internal error: {type(e).__name__}: {e}", pid=None)
            if emit is not None:
                emit("task.log", {"task_id": tid, "line": traceback.format_exc()})
        store.refresh_job_status(job)
        write_manifest(store, job, opts.output_dir, input_root)
    store.refresh_job_status(job)
    return job
