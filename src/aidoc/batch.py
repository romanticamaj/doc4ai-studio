"""Batch conversion (spec §5): in-process queue (index A10), cache via DB, atomic manifest."""
from __future__ import annotations

import hashlib
import json
import threading
import traceback
from collections.abc import Callable
from pathlib import Path

from aidoc import fsops, names
from aidoc.config import AidocConfig
from aidoc.models import ConvertOptions, ErrorKind, JobStatus, TaskStatus
from aidoc.output import planned_output_dir
from aidoc.pipeline import run_task
from aidoc.store import Store, TaskBusyError

IGNORED_NAMES = {"_manifest.jsonl", "chunks.jsonl"}
_FINISHED = {TaskStatus.done.value, TaskStatus.low.value, TaskStatus.skipped.value}


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


def manifest_rows(store: Store, job_id: str, input_root: Path | None = None,
                  duplicates: list[tuple[Path, str]] | None = None) -> list[dict]:
    """One row per task; `duplicates` = (source, task_id) for identical-content copies that were not re-run."""
    rows, by_task = [], {}
    for t in store.list_tasks(job_id):
        q = t.get("quality") or {}
        row = {"source": _source_label(t["source_path"], input_root), "status": t["status"], "engine": t["engine"],
               "score": q.get("score"), "level": q.get("level")}
        if t["status"] == TaskStatus.failed.value:
            row["error"] = f"{t['error_kind']}: {t['error_msg']}"
        rows.append(row)
        by_task[t["id"]] = row
    for src, tid in duplicates or []:
        orig = by_task.get(tid)
        if orig is None:
            continue
        rows.append({"source": _source_label(str(src), input_root), "status": TaskStatus.skipped.value,
                     "engine": orig["engine"], "score": orig["score"], "level": orig["level"],
                     "duplicate_of": orig["source"]})
    return rows


def write_manifest(store: Store, job_id: str, out_root: Path, input_root: Path | None = None,
                   duplicates: list[tuple[Path, str]] | None = None) -> None:
    rows = manifest_rows(store, job_id, input_root, duplicates)
    fsops.atomic_write_lines(Path(out_root) / "_manifest.jsonl", [json.dumps(r, ensure_ascii=False) for r in rows])


def register_source(store: Store, job_id: str, src: Path, opts: ConvertOptions) -> tuple[str, bool]:
    """Create (or reuse) the task for one input. An input that vanished or cannot be read (locked, no permission)
    still gets a row, ending as failed(input: source_missing | source_unreadable), so one bad file never aborts
    the batch; so does a file whose task another process is converting right now (already_converting).
    Such rows use a placeholder key ("!" + 63 hex, from path/job/code) instead of a content sha256."""
    src = Path(src)
    try:
        st = src.stat()
        sha = names.file_sha256(src)
    except FileNotFoundError as e:
        code, err = "source_missing", e
    except OSError as e:
        code, err = "source_unreadable", e
    else:
        out_dir = planned_output_dir(store, opts.output_dir, src, sha)
        try:
            return store.create_task(job_id, str(src), sha, st.st_size, st.st_mtime, opts.lang, str(out_dir))
        except TaskBusyError as e:
            code, err = "already_converting", f"{src} (task {e.task_id} is running in another process)"
    key = "!" + hashlib.sha256(f"{src}|{job_id}|{code}".encode()).hexdigest()[1:]
    out_dir = Path(opts.output_dir) / names.sanitize_stem(src.stem)
    tid, reused = store.create_task(job_id, str(src), key, 0, 0.0, opts.lang, str(out_dir))
    store.update_task(tid, status=TaskStatus.failed, error_kind=ErrorKind.input, error_msg=f"{code}: {err}", pid=None)
    return tid, reused


def run_batch(store: Store, config: AidocConfig, engines: dict, inputs: list[Path], opts: ConvertOptions,
              emit: Callable[[str, dict], None] | None = None, cancel: threading.Event | None = None,
              input_root: Path | None = None) -> str:
    job = store.create_job(opts, "cli")
    task_ids: list[str] = []
    duplicates: list[tuple[Path, str]] = []             # identical content that maps to the same output dir
    first_source: dict[str, Path] = {}
    try:
        for src in inputs:                              # create every task first (resume key: sha256, output_dir)
            src = Path(src).resolve()
            tid, _ = register_source(store, job, src, opts)
            if tid in first_source:                     # create_task re-parented our own row to this copy:
                store.update_task(tid, source_path=str(first_source[tid]))   # keep the first source on the row
                duplicates.append((src, tid))
                continue
            first_source[tid] = src
            if store.get_task(tid)["status"] != TaskStatus.failed.value:
                task_ids.append(tid)
    except BaseException as e:                          # never leave a job (or its tasks) queued forever
        for tid in first_source:
            if store.get_task(tid)["status"] == TaskStatus.queued.value:
                store.update_task(tid, status=TaskStatus.failed, error_kind=ErrorKind.engine,
                                  error_msg=f"batch aborted while collecting inputs: {type(e).__name__}: {e}")
        store.set_job_status(job, JobStatus.done)
        raise
    store.refresh_job_status(job)
    write_manifest(store, job, opts.output_dir, input_root, duplicates)
    for tid in task_ids:
        if cancel is not None and cancel.is_set():
            store.update_task(tid, status=TaskStatus.cancelled)
            continue
        if store.get_task(tid)["status"] in _FINISHED:  # finished meanwhile (e.g. by another process)
            continue
        try:
            run_task(store, tid, engines, config, emit=emit, cancel=cancel)
        except KeyboardInterrupt:                       # Ctrl+C: the rest of the job is cancelled, not left queued
            for rest in store.list_tasks(job, status=TaskStatus.queued):
                store.update_task(rest["id"], status=TaskStatus.cancelled)
            store.set_job_status(job, JobStatus.cancelled)
            write_manifest(store, job, opts.output_dir, input_root, duplicates)
            raise
        except Exception as e:  # noqa: BLE001  a crashing task must never stop the batch
            store.update_task(tid, status=TaskStatus.failed, error_kind=ErrorKind.engine,
                              error_msg=f"internal error: {type(e).__name__}: {e}", pid=None)
            if emit is not None:
                emit("task.log", {"task_id": tid, "line": traceback.format_exc()})
        store.refresh_job_status(job)
        write_manifest(store, job, opts.output_dir, input_root, duplicates)
    store.refresh_job_status(job)
    return job


def run_batch_via_server(client, inputs: list[Path], opts: ConvertOptions, print_fn: Callable[[str], None] = print,
                         input_root: Path | None = None, store_factory: Callable[[], Store] | None = None) -> int:
    """Forward a batch to the running server (spec §8.1) and follow it. One line per task status change.
    Ctrl+C detaches (the server keeps the job). Exit code: 2 when any task failed, else 0."""
    print_fn(f"forwarding to server {client.base_url}")
    job = client.create_job(list(inputs), opts)
    job_id = job["id"]
    seen: dict[str, str] = {}

    def on_event(kind: str, p: dict) -> None:
        if kind != "task.updated" or seen.get(p["id"]) == p["status"]:
            return
        seen[p["id"]] = p["status"]
        extra = f"  {p.get('error_kind')}: {p.get('error_msg')}" if p["status"] == TaskStatus.failed.value else ""
        print_fn(f"{p['status']:10} {_source_label(p['source_path'], input_root)} {p.get('engine') or ''}{extra}"
                 .rstrip())
    from aidoc.client import ServerGone
    try:
        final = client.follow_job(job_id, on_event, threading.Event())
    except KeyboardInterrupt:
        print_fn(f"detached; job {job_id} keeps running (aidoc cancel {job_id} to cancel)")
        return 0
    except ServerGone:
        print_fn(f"server stopped; job {job_id} resumes when `aidoc serve` starts again")
        return 1
    detail = client.get_job(job_id)
    for t in detail["tasks"]:                           # anything the stream did not show (e.g. already cached)
        on_event("task.updated", t)
    if store_factory is not None:
        store = store_factory()
        try:
            write_manifest(store, job_id, opts.output_dir, input_root)
        finally:
            store.close()
    counts: dict[str, int] = {}
    for t in detail["tasks"]:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    print_fn(f"job {job_id} {final}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 2 if counts.get(TaskStatus.failed.value) else 0
