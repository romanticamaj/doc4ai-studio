"""Single-task conversion pipeline (P1: one segment, no transient retry). Spec §4, §5, §8.2."""
from __future__ import annotations
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from aidoc import paths
from aidoc.config import AidocConfig
from aidoc.engines.base import EngineError
from aidoc.engines.host import compute_timeout
from aidoc.fsops import FsBusyError
from aidoc.models import (Attempt, ConvertOptions, ErrorKind, NormalizedResult, ProbeResult, QualityResult,
                          SegmentStatus, TaskStatus)
from aidoc.names import sanitize_stem
from aidoc.normalize import normalize
from aidoc.output import OutputWriter, build_sidecar, choose_output_dir, lookup_cached
from aidoc.probe import probe_file
from aidoc.quality import assess
from aidoc.router import route
from aidoc.store import Store

Emit = Callable[[str, dict], None]


def stage_paths(task_id: str) -> tuple[Path, Path]:
    work = paths.data_dir() / "work" / task_id
    return work, work / "seg_0"


@dataclass
class _Candidate:
    engine: str
    norm: NormalizedResult
    quality: QualityResult
    page_count: int | None


@dataclass
class PipelineContext:
    store: Store
    task_id: str
    config: AidocConfig
    emit: Emit | None
    cancel: threading.Event | None
    started: float = field(default_factory=time.time)

    def set(self, **fields) -> dict:
        self.store.update_task(self.task_id, **fields)
        row = self.store.get_task(self.task_id)
        if self.emit is not None:
            self.emit("task.updated", row)
        return row

    def log(self, line: str) -> None:
        if self.emit is not None and line:
            self.emit("task.log", {"task_id": self.task_id, "line": line, "ts": time.time()})

    def cancelled(self) -> bool:
        return self.cancel is not None and self.cancel.is_set()


def _fail(ctx: PipelineContext, kind: ErrorKind, msg: str) -> TaskStatus:
    ctx.set(status=TaskStatus.failed, error_kind=kind, error_msg=msg, pid=None)
    return TaskStatus.failed


def _cancel(ctx: PipelineContext) -> TaskStatus:
    ctx.set(status=TaskStatus.cancelled, pid=None)
    return TaskStatus.cancelled


def _set_output_dir(store: Store, task: dict, output_dir: Path) -> None:
    if task["output_dir"] == str(output_dir):
        return
    try:
        store.update_task(task["id"], output_dir=str(output_dir))
    except sqlite3.IntegrityError:
        # an older row already owns (sha256, output_dir): P1 keeps only the newest attempt for that key
        other = [t for t in store.list_tasks() if t["sha256"] == task["sha256"] and t["output_dir"] == str(output_dir)]
        for t in other:
            store.con.execute("DELETE FROM segments WHERE task_id=?", (t["id"],))
            store.con.execute("DELETE FROM tasks WHERE id=?", (t["id"],))
        store.update_task(task["id"], output_dir=str(output_dir))


def _write_output(ctx: PipelineContext, task: dict, opts: ConvertOptions, probe: ProbeResult, cand: _Candidate,
                  output_dir: Path, work_src: Path, segments: list[dict]) -> TaskStatus:
    store = ctx.store
    tried = store.get_task(ctx.task_id)["tried"]
    stem = output_dir.name
    sidecar = build_sidecar(source=task["source_path"], sha256=task["sha256"],
                            pages=probe.pages if probe.pages is not None else cand.page_count,
                            engine=cand.engine, tried=tried, segments=len(segments) or 1, probe=probe.to_json(),
                            quality=cand.quality.to_json(), lang=opts.lang, elapsed_s=time.time() - ctx.started)
    writer = OutputWriter(opts.output_dir, ctx.task_id, stem)
    try:
        writer.write(cand.norm.markdown, cand.norm.assets, sidecar)
        writer.finalize(output_dir)
    except FsBusyError as e:
        writer.discard()
        return _fail(ctx, ErrorKind.transient, f"output busy: {e}")
    status = TaskStatus.done if cand.quality.level == "ok" else TaskStatus.low
    retention = ctx.config.general.work_retention_days
    store.upsert_document(sha256=task["sha256"], source_path=task["source_path"], output_dir=str(output_dir),
                          engine=cand.engine, quality=cand.quality.to_json(), pages=sidecar["pages"], lang=opts.lang,
                          aidoc_version=sidecar["aidoc_version"], status=cand.quality.level,
                          work_copy_path=str(work_src), work_copy_expires_at=time.time() + retention * 86400,
                          created_at=time.time())
    for seg in segments:
        store.update_segment(seg["id"], status=SegmentStatus.done, output_path=str(output_dir))
    ctx.set(status=status, engine=cand.engine, quality=cand.quality.to_json(), output_dir=str(output_dir),
            error_kind=None, error_msg=None, pid=None)
    return status


def run_task(store: Store, task_id: str, engines: dict, config: AidocConfig, emit: Emit | None = None,
             cancel: threading.Event | None = None) -> TaskStatus:
    ctx = PipelineContext(store, task_id, config, emit, cancel)
    task = store.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    job = store.get_job(task["job_id"])
    opts = ConvertOptions.from_json(job["options"])
    if ctx.cancelled():
        return _cancel(ctx)

    # 1. probe + cache
    ctx.set(status=TaskStatus.probing, error_kind=None, error_msg=None)
    src = Path(task["source_path"])
    if not src.is_file():
        return _fail(ctx, ErrorKind.input, f"source_missing: {src}")
    probe = probe_file(src)
    if probe.error:
        return _fail(ctx, ErrorKind.input, probe.error)
    output_dir = choose_output_dir(store, opts.output_dir, sanitize_stem(src.stem), task["sha256"])
    _set_output_dir(store, task, output_dir)
    task = store.get_task(task_id)
    if not opts.force:
        hit = lookup_cached(store, task["sha256"], output_dir)
        if hit is not None:
            level = (hit.get("quality") or {}).get("level") or hit.get("status")
            if level == "ok" or not opts.retry_low:
                ctx.set(status=TaskStatus.skipped, engine=hit.get("engine"), quality=hit.get("quality"))
                return TaskStatus.skipped

    # 2. route
    decision = route(probe, opts, {n: e.available() for n, e in engines.items()})
    if not decision.engines:
        if decision.reason == "no_engine_available":
            missing = " ".join(decision.missing) or "all"
            return _fail(ctx, ErrorKind.engine, f"no_engine_available; run aidoc setup {missing}")
        return _fail(ctx, ErrorKind.input, decision.reason)
    if decision.missing:
        ctx.log(f"engines not installed, skipped: {', '.join(decision.missing)} (run aidoc setup <engine>)")

    # 3. stage work copy + segment row
    work_dir, seg_dir = stage_paths(task_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    work_src = work_dir / f"src{src.suffix.lower()}"
    shutil.copy2(src, work_src)
    ctx.set(work_path=str(work_src))
    segments = store.list_segments(task_id)
    if segments:
        store.reset_segments(task_id)
    else:
        store.create_segments(task_id, [(1, probe.pages) if probe.pages else (None, None)])
    segments = store.list_segments(task_id)

    # 4. engines in order
    timeout = compute_timeout(probe.pages, probe.size, opts, config.limits)
    best: _Candidate | None = None
    attempt_no = task["attempt"]
    for name in decision.engines:
        if ctx.cancelled():
            return _cancel(ctx)
        engine = engines[name]
        attempt_no += 1
        ctx.set(status=TaskStatus.converting, engine=name, attempt=attempt_no)
        for seg in segments:
            store.update_segment(seg["id"], status=SegmentStatus.converting, attempt=attempt_no)
        eng_dir = seg_dir / f"{attempt_no}_{name}"

        def on_progress(frac: float, line: str) -> None:
            if line:
                ctx.log(line)

        try:
            raw = engine.convert(work_src, eng_dir, opts, None, on_progress, timeout_s=timeout, probe=probe,
                                 on_start=lambda pid: store.update_task(task_id, pid=pid))
        except EngineError as e:
            store.update_task(task_id, pid=None)
            store.append_attempt(task_id, Attempt(engine=name, attempt=attempt_no, score=None, reasons=[],
                                                  error_kind=e.kind.value, error_msg=e.message[:1000]))
            ctx.log(f"{name} failed ({e.kind.value}): {e.message[:300]}")
            if e.kind == ErrorKind.input:
                return _fail(ctx, ErrorKind.input, f"{name}: {e.message[:500]}")
            continue
        store.update_task(task_id, pid=None)
        ctx.set(status=TaskStatus.checking)
        norm = normalize(raw, 0, 0)
        q = assess(norm.markdown, probe, probe.pages)
        store.append_attempt(task_id, Attempt(engine=name, attempt=attempt_no, score=q.score, reasons=q.reasons))
        cand = _Candidate(name, norm, q, raw.page_count)
        if q.level == "ok":
            return _write_output(ctx, task, opts, probe, cand, output_dir, work_src, segments)
        ctx.log(f"{name} quality low ({q.score}): {', '.join(q.reasons)}")
        if best is None or q.score > best.quality.score:
            best = cand

    # 5. nothing passed
    if best is not None:
        return _write_output(ctx, task, opts, probe, best, output_dir, work_src, segments)
    for seg in segments:
        store.update_segment(seg["id"], status=SegmentStatus.failed)
    return _fail(ctx, ErrorKind.engine, "all engines failed")
