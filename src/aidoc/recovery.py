"""Startup recovery (spec §8.4). Runs before any CLI batch/convert (and, in P3, server) work starts.

Order is mandatory: (1) kill orphan runners, (2) requeue interrupted tasks, (3) clean .tmp/ and .trash/,
(4) mark orphaned documents, (5) purge expired work copies. Tasks another live process is running right now
(its aidoc.tasklock lock is held) are left completely alone.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

import psutil

from aidoc import fsops, procs
from aidoc.models import TERMINAL_TASK, SegmentStatus, TaskStatus
from aidoc.output import sidecar_path
from aidoc.sources import purge_expired_work_copies

_ACTIVE = [TaskStatus.probing.value, TaskStatus.converting.value, TaskStatus.checking.value]


def _key(p: Path) -> str:
    return os.path.normcase(os.path.realpath(str(p)))


def recover_on_startup(store, config, log: Callable[[str], None] = print) -> dict:
    res: dict = {"killed": [], "requeued": [], "segments_reset": 0, "tmp_removed": [], "trash_removed": [],
                 "orphaned": [], "work_purged": 0}
    interrupted = [t for t in store.list_tasks(status_in=_ACTIVE) if not store.task_is_live(t["id"])]
    live_ids = {t["id"] for t in store.list_tasks(status_in=_ACTIVE)} - {t["id"] for t in interrupted}

    # (1) orphan runners: only processes whose command line is an aidoc runner script (PIDs get reused)
    for t in interrupted:
        pid = t["pid"]
        if pid is None:
            continue
        if psutil.pid_exists(pid) and procs.is_aidoc_runner(pid):
            procs.kill_tree(pid)
            res["killed"].append(pid)
            log(f"killed orphan runner pid={pid} (task {t['id']})")
        store.update_task(t["id"], pid=None)

    # (2) requeue; segments: only the unfinished (converting) ones are reset, done ones are kept
    for t in interrupted:
        store.update_task(t["id"], status=TaskStatus.queued, pid=None)
        res["requeued"].append(t["id"])
        for seg in store.list_segments(t["id"]):
            if seg["status"] == SegmentStatus.converting.value:
                store.update_segment(seg["id"], status=SegmentStatus.queued, output_path=None)
                res["segments_reset"] += 1
    if res["requeued"]:
        log(f"requeued {len(res['requeued'])} interrupted task(s)")

    # (3) .tmp/<x> without an unfinished task, and .trash/ (except a live task's in-flight three-step replace)
    terminal = {s.value for s in TERMINAL_TASK}
    all_tasks = store.list_tasks()
    keep_tmp = {t["id"] for t in all_tasks if t["status"] not in terminal} | live_ids
    roots, seen = [], set()
    for p in [*(Path(t["output_dir"]).parent for t in all_tasks), config.output_root()]:
        k = _key(p)
        if k not in seen:
            seen.add(k)
            roots.append(Path(p))
    for root in roots:
        for sub, bucket, keep in ((".tmp", "tmp_removed", keep_tmp), (".trash", "trash_removed", live_ids)):
            d = root / sub
            if not d.is_dir():
                continue
            for x in sorted(d.iterdir()):
                if x.name in keep:
                    continue
                try:
                    fsops.remove_tree(x) if x.is_dir() else x.unlink()
                    res[bucket].append(str(x))
                except OSError as e:
                    log(f"could not remove {x}: {e}")
            try:
                d.rmdir()                                   # only when now empty
            except OSError:
                pass
    if res["tmp_removed"] or res["trash_removed"]:
        log(f"removed {len(res['tmp_removed'])} stale temp dir(s), {len(res['trash_removed'])} trash item(s)")

    # (4) documents whose output (sidecar) is gone from disk
    for doc in store.list_documents():
        if doc["status"] in ("ok", "warn", "low") and not sidecar_path(Path(doc["output_dir"])).is_file():
            store.set_document_status(doc["id"], "orphaned")
            res["orphaned"].append(doc["id"])
    if res["orphaned"]:
        log(f"marked {len(res['orphaned'])} document(s) orphaned (output missing)")

    # (5) work copies past work_retention_days
    res["work_purged"] = purge_expired_work_copies(store, time.time())
    if res["work_purged"]:
        log(f"purged {res['work_purged']} expired work cop(ies)")
    return res
