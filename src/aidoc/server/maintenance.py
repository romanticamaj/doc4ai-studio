"""Periodic housekeeping while the server runs (spec §8.1 events retention, §8.5 work copies, §8.7 uploads)."""
from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path

from aidoc import fsops
from aidoc.models import TaskStatus
from aidoc.sources import purge_expired_work_copies

_DEAD = {TaskStatus.failed.value, TaskStatus.cancelled.value, TaskStatus.skipped.value, TaskStatus.done.value,
         TaskStatus.low.value}


def purge_stale_work_dirs(store, data_dir: Path, retention_days: float, now: float) -> int:
    """data/work/<task_id>/ of tasks that ended (failed, cancelled, ...) or no longer exist, older than the retention
    period and not a document's retained source copy. Queued / running tasks' dirs (uploads!) are never touched."""
    work = Path(data_dir) / "work"
    if not work.is_dir():
        return 0
    referenced = {str(Path(d["work_copy_path"])) for d in store.list_documents() if d.get("work_copy_path")}
    cutoff = now - retention_days * 86400
    n = 0
    for d in work.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        if str(d) in referenced:
            continue
        task = store.get_task(d.name)
        if task is not None:
            if task["status"] not in _DEAD or task["updated_at"] > cutoff or store.task_is_live(task["id"]):
                continue
        else:
            try:
                if d.stat().st_mtime > cutoff:
                    continue
            except OSError:
                continue
        try:
            fsops.remove_tree(d)
        except OSError:
            continue
        if task is not None and task.get("work_path"):
            store.update_task(task["id"], work_path=None)
        n += 1
    return n


class Maintenance:
    def __init__(self, ctx, interval_s: float = 600.0, keep_events: int = 10000):
        self.ctx = ctx
        self.interval_s = interval_s
        self.keep_events = keep_events
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> dict:
        ctx, now = self.ctx, time.time()
        res = {"uploads_purged": ctx.uploads.purge_stale(),
               "work_copies_purged": purge_expired_work_copies(ctx.store, now),
               "work_dirs_removed": purge_stale_work_dirs(ctx.store, ctx.config.data_dir,
                                                          ctx.config.general.work_retention_days, now)}
        mcp = ctx.config.mcp
        res["mcp_calls_pruned"] = ctx.store.prune_mcp_calls(older_than_ts=now - mcp.call_log_retention_days * 86400,
                                                            max_rows=mcp.call_log_max_rows)
        ctx.store.prune_events(keep=self.keep_events)
        ctx.bus.publish("system.updated", None, {"maintenance": res})
        return res

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.run_once()
            except Exception:  # noqa: BLE001  housekeeping must never take the server down
                traceback.print_exc()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="aidoc-maintenance", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
