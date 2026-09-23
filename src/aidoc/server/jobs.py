"""The single job queue (spec §8.1): one worker thread runs `pipeline.run_task` for one task at a time.

State goes to SQLite first (the pipeline and Store), then out on the EventBus. Cancel interrupts at once (P2's
interruptible runner and backoff); pause lets the running segment finish, then the task goes back to `queued`
(index A16). Server shutdown also sends the running task back to `queued` — it is not a user cancel."""
from __future__ import annotations

import threading
import traceback
from pathlib import Path

from aidoc.engines.registry import get_engines
from aidoc.models import TERMINAL_TASK, ErrorKind, JobStatus, TaskStatus
from aidoc.names import file_sha256
from aidoc.pipeline import run_task
from aidoc.server.serialize import serialize_job, serialize_segment, serialize_task

_TERMINAL = {s.value for s in TERMINAL_TASK}
_ACTIVE = {TaskStatus.probing.value, TaskStatus.converting.value, TaskStatus.checking.value}
_FINISHED = {TaskStatus.done.value, TaskStatus.low.value, TaskStatus.skipped.value}


class RetryError(Exception):
    def __init__(self, status: int, error: str, **extra):
        super().__init__(error)
        self.status, self.error, self.extra = status, error, extra


class JobQueue:
    def __init__(self, ctx):
        self.ctx = ctx
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._pause_event = threading.Event()
        self._state = threading.RLock()          # picking a task vs. cancel_job
        self._run_lock = threading.Lock()        # one task at a time (worker thread or process_next in tests)
        self._thread: threading.Thread | None = None
        self._cancel: threading.Event | None = None
        self._running_job: str | None = None
        self._shutdown_requeue = False
        self.running_task_id: str | None = None

    # ------------------------------------------------------------ events
    def _emit(self, kind: str, payload: dict) -> None:
        store, bus = self.ctx.store, self.ctx.bus
        if kind == "task.updated":
            row = store.get_task(payload["id"]) or payload
            payload = serialize_task(store, row, with_segments=False)
        elif kind == "segment.updated":
            payload = serialize_segment(payload, payload.get("task_id"))
        bus.publish(kind, payload.get("id") or payload.get("task_id"), payload)

    def publish_job(self, job_id: str) -> None:
        job = self.ctx.store.get_job(job_id)
        if job is not None:
            self.ctx.bus.publish("job.updated", job_id, serialize_job(self.ctx.store, job))

    def publish_task(self, task_id: str) -> None:
        t = self.ctx.store.get_task(task_id)
        if t is not None:
            self._emit("task.updated", t)

    def publish_queue(self) -> None:
        self.ctx.bus.publish("queue.updated", None, self.snapshot())

    # ------------------------------------------------------------ state
    @property
    def paused(self) -> bool:
        return self._pause_event.is_set()

    def snapshot(self) -> dict:
        return {"paused": self.paused, "length": self.ctx.store.count_queued_tasks(),
                "running_task_id": self.running_task_id}

    def pause(self) -> None:
        self._pause_event.set()
        self.publish_queue()

    def resume(self) -> None:
        self._pause_event.clear()
        self._wake.set()
        self.publish_queue()

    def wake(self) -> None:
        self._wake.set()

    # ------------------------------------------------------------ worker
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._loop, name="aidoc-queue", daemon=True)
        self._thread.start()
        self.publish_queue()

    def stop(self, timeout: float = 10) -> None:
        self._stopping.set()
        with self._state:
            if self._cancel is not None:
                self._shutdown_requeue = True
                self._cancel.set()
        self._wake.set()
        th = self._thread
        if th is not None:
            th.join(timeout)
        self._thread = None

    def _loop(self) -> None:
        while not self._stopping.is_set():
            tid = None
            try:
                if not self.paused:
                    tid = self.process_next()
            except Exception:  # noqa: BLE001  the worker must survive anything
                traceback.print_exc()
            if tid is None:
                self._wake.wait(1.0)
                self._wake.clear()

    def process_next(self) -> str | None:
        """Run one queued task synchronously; None when paused, stopping or nothing is queued."""
        store = self.ctx.store
        with self._run_lock:
            with self._state:
                if self.paused or self._stopping.is_set():
                    return None
                task = self._pick()
                if task is None:
                    return None
                tid, job_id = task["id"], task["job_id"]
                flags_before = dict(task.get("flags") or {})
                self._cancel = threading.Event()
                self._shutdown_requeue = False
                self.running_task_id, self._running_job = tid, job_id
                cancel = self._cancel
                store.refresh_job_status(job_id)
            self.publish_job(job_id)
            self.publish_queue()
            status = None
            try:
                status = run_task(store, tid, get_engines(self.ctx.config), self.ctx.config, emit=self._emit,
                                  cancel=cancel, pause=self._pause_event)
            except Exception as e:  # noqa: BLE001  run_task catches pipeline errors; this is the last net
                store.update_task(tid, status=TaskStatus.failed, error_kind=ErrorKind.engine,
                                  error_msg=f"internal error: {type(e).__name__}: {e}", pid=None)
                self.publish_task(tid)
            finally:
                with self._state:
                    shutdown = self._shutdown_requeue
                    self.running_task_id = self._running_job = None
                    self._cancel = None
                if shutdown and status == TaskStatus.cancelled:
                    store.requeue_task(tid, reset_segments=False)      # resumes with its done segments
                    if flags_before:                                   # the run never finished: keep e.g. force
                        store.set_task_flags(tid, flags_before)
                    self.publish_task(tid)
                store.refresh_job_status(job_id)
                self.publish_job(job_id)
                self.publish_queue()
            return tid

    def _pick(self) -> dict | None:
        """Oldest runnable queued task; one another process holds the liveness lock of (an in-process CLI run)
        is skipped instead of being picked, refused by run_task and picked again in a tight loop."""
        store = self.ctx.store
        first = store.next_queued_task()
        if first is None or not store.task_is_live(first["id"]):
            return first
        for t in store.list_tasks(status="queued"):
            job = store.get_job(t["job_id"])
            if job is not None and job["status"] != JobStatus.cancelled.value and not store.task_is_live(t["id"]):
                return t
        return None

    # ------------------------------------------------------------ commands
    def cancel_job(self, job_id: str) -> None:
        """Cancel a queued or running job. A job that already finished (`done`) or was cancelled is left as it
        is: cancelling it is a no-op (the API returns the unchanged job)."""
        store = self.ctx.store
        with self._state:
            job = store.get_job(job_id)
            if job is None or job["status"] in (JobStatus.done.value, JobStatus.cancelled.value):
                return
            store.set_job_status(job_id, JobStatus.cancelled)
            cancelled = store.cancel_queued_tasks(job_id)
            running_here = self._running_job == job_id and self._cancel is not None
            if running_here:
                self._cancel.set()
        for tid in cancelled:
            self.publish_task(tid)
        self.publish_job(job_id)
        self.publish_queue()

    def busy_engine(self, engine: str) -> str | None:
        """The engine of the running task if setting up `engine` ("all" or a name) would rebuild it; a task that
        has not chosen its engine yet (probing) blocks every setup. None when nothing conflicts."""
        tid = self.running_task_id
        if tid is None:
            return None
        task = self.ctx.store.get_task(tid)
        if task is None:
            return None
        running = task.get("engine")
        if running is None:
            return "unknown"
        return running if engine in ("all", running) else None

    def retry_task(self, task_id: str, use_new_version: bool = False, retry_low: bool = False) -> dict:
        store = self.ctx.store
        task = store.get_task(task_id)
        if task is None:
            raise RetryError(404, "not_found")
        if task_id == self.running_task_id or (task["status"] in _ACTIVE and store.task_is_live(task_id)):
            raise RetryError(409, "task_running")
        new_sha = None
        if use_new_version:
            if not Path(task["source_path"]).is_absolute():          # an upload: there is no "new version" on disk
                raise RetryError(409, "not_a_local_source")
            try:
                new_sha = file_sha256(task["source_path"])
            except OSError:
                raise RetryError(410, "source_missing", path=task["source_path"]) from None
        was_finished = task["status"] in _FINISHED
        with self._state:
            # a finished task starts over (its done segments are the old result); failed/cancelled ones resume
            store.requeue_task(task_id, reset_segments=use_new_version or was_finished, new_sha=new_sha)
            if retry_low or was_finished or use_new_version:
                store.set_task_flags(task_id, {"force": True})
            job = store.get_job(task["job_id"])
            if job is not None and job["status"] == JobStatus.cancelled.value:
                store.set_job_status(task["job_id"], JobStatus.queued)       # a retried task un-cancels its job
            store.refresh_job_status(task["job_id"])
        self.publish_task(task_id)
        self.publish_job(task["job_id"])
        self.publish_queue()
        self.wake()
        return store.get_task(task_id)
