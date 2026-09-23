"""Single-task conversion pipeline: segments, quick checks, fallback, resume. Spec §4, §5, §8.2, §8.3."""
from __future__ import annotations

import dataclasses
import json
import shutil
import sqlite3
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from aidoc import fsops, paths
from aidoc.config import AidocConfig
from aidoc.engines.base import EngineCancelled, EngineError
from aidoc.engines.host import compute_timeout
from aidoc.fsops import FsBusyError
from aidoc.models import (
    TERMINAL_TASK,
    Attempt,
    ConvertOptions,
    ErrorKind,
    NormalizedResult,
    ProbeResult,
    QualityResult,
    SegmentStatus,
    TableEdge,
    TaskStatus,
)
from aidoc.normalize import normalize
from aidoc.output import OutputWriter, build_sidecar, lookup_cached, planned_output_dir
from aidoc.probe import probe_file
from aidoc.quality import assess
from aidoc.retry import RetryPolicy
from aidoc.router import route
from aidoc.segment import SegmentPart, merge_segments, plan_segments, segment_dir, split_pdf
from aidoc.sources import SourceError, stage_source
from aidoc.store import Store
from aidoc.tasklock import TaskLock, lock_path

Emit = Callable[[str, dict], None]
SEGMENT_FILES = ("normalized.md", "assets.json", "edges.json")
RETRY_POLICY = RetryPolicy()


def options_key(opts: ConvertOptions) -> str:
    """The options that change what an engine produces; done segments made with other values are redone."""
    return json.dumps({"lang": opts.lang, "mineru_tier": opts.mineru_tier, "docling_ocr": opts.docling_ocr},
                      sort_keys=True)


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
    pause: threading.Event | None = None      # set = queue paused: stop between segments, task back to queued
    started: float = field(default_factory=time.time)
    # filled in by _run_task once known
    opts: ConvertOptions | None = None
    probe: ProbeResult | None = None
    work_dir: Path | None = None
    work_src: Path | None = None
    attempt: int = 0

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

    def paused(self) -> bool:
        return self.pause is not None and self.pause.is_set()

    def segment_updated(self, seg_id: str) -> None:
        if self.emit is not None:
            self.emit("segment.updated", {**self.store.get_segment(seg_id), "task_id": self.task_id})

    def progress(self) -> None:
        if self.emit is None:
            return
        segs = self.store.list_segments(self.task_id)
        total = sum((s["page_end"] - s["page_start"] + 1) for s in segs if s["page_start"] is not None)
        done = sum((s["page_end"] - s["page_start"] + 1) for s in segs
                   if s["page_start"] is not None and s["status"] == SegmentStatus.done.value)
        if not total:
            total, done = len(segs), sum(1 for s in segs if s["status"] == SegmentStatus.done.value)
        self.emit("task.updated", {**self.store.get_task(self.task_id),
                                   "progress": {"pages_done": done, "pages_total": total}})


class TaskPaused(Exception):
    """The queue was paused; raised between segments (index A16: the current segment finishes first)."""


def _fail(ctx: PipelineContext, kind: ErrorKind, msg: str) -> TaskStatus:
    ctx.set(status=TaskStatus.failed, error_kind=kind, error_msg=msg, pid=None)
    return TaskStatus.failed


def _requeue_paused(ctx: PipelineContext) -> TaskStatus:
    ctx.log("queue paused: task goes back to the queue; finished segments are kept")
    ctx.set(status=TaskStatus.queued, pid=None)
    return TaskStatus.queued


def _cancel(ctx: PipelineContext) -> TaskStatus:
    for seg in ctx.store.list_segments(ctx.task_id):       # the interrupted segment goes back to the queue
        if seg["status"] == SegmentStatus.converting.value:
            ctx.store.update_segment(seg["id"], status=SegmentStatus.queued, output_path=None)
            ctx.segment_updated(seg["id"])
    ctx.set(status=TaskStatus.cancelled, pid=None)
    return TaskStatus.cancelled


def _set_output_dir(store: Store, task: dict, output_dir: Path) -> None:
    if task["output_dir"] == str(output_dir):
        return
    try:
        store.update_task(task["id"], output_dir=str(output_dir))
    except sqlite3.IntegrityError:
        # an older row owns (sha256, output_dir). Callers normally create tasks with the final dir
        # (output.planned_output_dir), so this only happens for hand-made provisional keys. A terminal row is
        # replaced (same rule as Store.create_task); an unfinished one is never deleted.
        other = [t for t in store.list_tasks() if t["sha256"] == task["sha256"] and t["output_dir"] == str(output_dir)]
        active = [t for t in other if t["status"] not in {s.value for s in TERMINAL_TASK}]
        if active:
            raise RuntimeError(f"output dir {output_dir} is owned by unfinished task {active[0]['id']}") from None
        for t in other:
            store.con.execute("DELETE FROM segments WHERE task_id=?", (t["id"],))
            store.con.execute("DELETE FROM tasks WHERE id=?", (t["id"],))
        store.update_task(task["id"], output_dir=str(output_dir))


# ---------------------------------------------------------------- segment files (data/work/<task>/seg_<idx>/)

def save_segment_part(seg_dir: Path, part: SegmentPart) -> None:
    """Write the three result files; the segment may be marked done only after this returns."""
    seg_dir = Path(seg_dir)
    fsops.atomic_write_text(seg_dir / "normalized.md", part.markdown)
    fsops.atomic_write_json(seg_dir / "assets.json", [[str(src), name] for src, name in part.assets])
    fsops.atomic_write_json(seg_dir / "edges.json", {
        "engine": part.engine, "opts_key": part.opts_key, "has_page_markers": part.has_page_markers,
        "page_count": part.page_count,
        "first_table": part.first_table.to_json() if part.first_table else None,
        "last_table": part.last_table.to_json() if part.last_table else None})


def load_segment_part(seg_dir: Path, seg_row: dict) -> SegmentPart | None:
    """The saved part of a done segment, or None when any file (or referenced asset) is missing/corrupt."""
    seg_dir = Path(seg_dir)
    try:
        md = (seg_dir / "normalized.md").read_text(encoding="utf-8")
        assets = [(Path(src), name) for src, name in json.loads((seg_dir / "assets.json").read_text(encoding="utf-8"))]
        edges = json.loads((seg_dir / "edges.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not all(src.is_file() for src, _ in assets):
        return None
    ft, lt = edges.get("first_table"), edges.get("last_table")
    return SegmentPart(idx=seg_row["idx"], page_start=seg_row["page_start"], page_end=seg_row["page_end"],
                       markdown=md, assets=assets, has_page_markers=bool(edges.get("has_page_markers")),
                       first_table=TableEdge.from_json(ft) if ft else None,
                       last_table=TableEdge.from_json(lt) if lt else None,
                       page_count=edges.get("page_count"), engine=edges.get("engine"),
                       opts_key=edges.get("opts_key"))


def _discard_segments(ctx: PipelineContext) -> None:
    """Switching engine: completed segments are void (spec §4 — one engine per document)."""
    ctx.store.reset_segments(ctx.task_id)
    for seg in ctx.store.list_segments(ctx.task_id):
        fsops.remove_tree(segment_dir(ctx.work_dir, seg["idx"]))


def _segment_probe(probe: ProbeResult, page_start: int, page_end: int) -> ProbeResult:
    # has_table_lines is a whole-document fact: a segment without tables must not fail the quick check for it
    return dataclasses.replace(probe, pages=page_end - page_start + 1, has_table_lines=False,
                               blank_pages=[p for p in probe.blank_pages if page_start <= p <= page_end])


# ---------------------------------------------------------------- one engine attempt (one runner session)

def run_engine_attempt(ctx: PipelineContext, engine, engine_opts: dict, *,
                       quick_check: bool = True) -> tuple[NormalizedResult, QualityResult, int | None]:
    """Convert every unfinished segment with one runner session, merge, assess the whole document.

    Raises EngineError / EngineCancelled. Done segments of the same engine are reused (resume)."""
    store, opts, probe = ctx.store, ctx.opts, ctx.probe
    segments = store.list_segments(ctx.task_id)
    multi = len(segments) > 1
    session = engine.open_session(opts, probe, engine_opts=engine_opts)
    ctx.attempt += 1
    ctx.set(status=TaskStatus.converting, engine=engine.name, attempt=ctx.attempt, pid=session.pid)
    ctx.log(f"{engine.name} runner started pid={session.pid} (attempt {ctx.attempt})")
    try:
        parts: list[SegmentPart] = []
        for seg in segments:
            sd = segment_dir(ctx.work_dir, seg["idx"])
            if seg["status"] == SegmentStatus.done.value:
                part = load_segment_part(sd, seg)
                if part is not None and part.engine == engine.name and part.opts_key == options_key(opts):
                    parts.append(part)
                    continue
            if ctx.cancelled():
                raise EngineCancelled("cancelled")
            if parts and ctx.paused():
                raise TaskPaused()
            store.update_segment(seg["id"], status=SegmentStatus.converting, attempt=ctx.attempt, output_path=None)
            ctx.segment_updated(seg["id"])
            for name in SEGMENT_FILES:                     # stale results of an earlier attempt
                (sd / name).unlink(missing_ok=True)
            fsops.remove_tree(sd / "raw")
            ps, pe = seg["page_start"], seg["page_end"]
            if multi and ps is not None:
                seg_src = sd / f"seg_{seg['idx']}.pdf"
                if not seg_src.is_file():
                    split_pdf(ctx.work_src, ps, pe, seg_src)
                pages = (ps, pe)
            else:
                seg_src, pages = ctx.work_src, None        # a single segment is the whole document
            seg_pages = (pe - ps + 1) if ps is not None else None
            timeout = compute_timeout(seg_pages, probe.size, opts, ctx.config.limits)

            def on_progress(frac: float, line: str) -> None:
                if line:
                    ctx.log(line)
            try:
                raw = session.convert(seg_src, sd, pages, on_progress, timeout_s=timeout, segment_idx=seg["idx"],
                                      cancel=ctx.cancel)
            except KeyboardInterrupt:
                session.kill()                     # Ctrl+C: the runner (own process group) must not outlive us
                raise
            offset = (ps - 1) if (pages is not None and ps) else 0
            norm = normalize(raw, offset, seg["idx"])
            if multi and quick_check and ps is not None:
                q = assess(norm.markdown, _segment_probe(probe, ps, pe), seg_pages)
                if q.level == "low":
                    raise EngineError(ErrorKind.engine, f"segment {seg['idx']} quick check failed: "
                                                        f"{', '.join(q.reasons)}")
            part = SegmentPart(idx=seg["idx"], page_start=ps, page_end=pe, markdown=norm.markdown, assets=norm.assets,
                               has_page_markers=raw.has_page_markers, first_table=raw.first_table,
                               last_table=raw.last_table, page_count=raw.page_count, engine=engine.name,
                               opts_key=options_key(opts))
            save_segment_part(sd, part)
            store.update_segment(seg["id"], status=SegmentStatus.done, output_path=str(sd))
            ctx.segment_updated(seg["id"])
            ctx.progress()
            parts.append(part)
    finally:
        session.close()
        store.update_task(ctx.task_id, pid=None)
    ctx.set(status=TaskStatus.checking)
    if multi:
        merged = merge_segments(parts)
    else:
        merged = NormalizedResult(markdown=parts[0].markdown, assets=parts[0].assets)
    page_count = probe.pages if probe.pages is not None else parts[0].page_count
    return merged, assess(merged.markdown, probe, probe.pages), page_count


# ---------------------------------------------------------------- output

def _keep_candidate(ctx: PipelineContext, cand: _Candidate) -> _Candidate:
    """Copy a low candidate's assets out of the segment dirs (they are discarded when the next engine runs)."""
    keep = ctx.work_dir / "best"
    fsops.remove_tree(keep)
    keep.mkdir(parents=True)
    assets = []
    for src, name in cand.norm.assets:
        shutil.copy2(src, keep / name)
        assets.append((keep / name, name))
    return dataclasses.replace(cand, norm=NormalizedResult(markdown=cand.norm.markdown, assets=assets))


def _write_output(ctx: PipelineContext, task: dict, cand: _Candidate, output_dir: Path) -> TaskStatus:
    store, opts, probe = ctx.store, ctx.opts, ctx.probe
    tried = store.get_task(ctx.task_id)["tried"]
    stem = output_dir.name
    n_segments = len(store.list_segments(ctx.task_id)) or 1
    sidecar = build_sidecar(source=task["source_path"], sha256=task["sha256"],
                            pages=probe.pages if probe.pages is not None else cand.page_count,
                            engine=cand.engine, tried=tried, segments=n_segments, probe=probe.to_json(),
                            quality=cand.quality.to_json(), lang=opts.lang, elapsed_s=time.time() - ctx.started)
    writer = OutputWriter(opts.output_dir, ctx.task_id, stem)
    try:
        writer.write(cand.norm.markdown, cand.norm.assets, sidecar)
        writer.finalize(output_dir)
    except FsBusyError as e:
        writer.discard()
        return _fail(ctx, ErrorKind.transient, f"output busy: {e}")
    except OSError as e:                      # disk full, invalid name, ...: never leave out/.tmp/<task> behind
        writer.discard()
        return _fail(ctx, ErrorKind.transient, f"output write failed: {e}")
    status = TaskStatus.done if cand.quality.level == "ok" else TaskStatus.low
    retention = ctx.config.general.work_retention_days
    store.upsert_document(sha256=task["sha256"], source_path=task["source_path"], output_dir=str(output_dir),
                          engine=cand.engine, quality=cand.quality.to_json(), pages=sidecar["pages"], lang=opts.lang,
                          aidoc_version=sidecar["aidoc_version"], status=cand.quality.level,
                          work_copy_path=str(ctx.work_dir), work_copy_expires_at=time.time() + retention * 86400,
                          created_at=time.time())
    ctx.set(status=status, engine=cand.engine, quality=cand.quality.to_json(), output_dir=str(output_dir),
            error_kind=None, error_msg=None, pid=None)
    _purge_intermediates(ctx)
    return status


def _purge_intermediates(ctx: PipelineContext) -> None:
    """After finalising, keep only the staged source (the Document view shows it for work_retention_days);
    segment dirs, raw engine output and kept candidates are no longer needed (P2 deferred M9)."""
    if ctx.work_dir is None or not ctx.work_dir.is_dir():
        return
    for child in ctx.work_dir.iterdir():
        if ctx.work_src is not None and child.name == ctx.work_src.name:
            continue
        try:
            fsops.remove_tree(child) if child.is_dir() else child.unlink()
        except OSError:
            pass                                  # best effort; maintenance removes the whole dir later


def _drop_unused_work_dir(ctx: PipelineContext) -> None:
    """A cache hit needs no work copy; keep it only if a document still references it."""
    if any(d.get("work_copy_path") == str(ctx.work_dir) for d in ctx.store.list_documents()):
        return
    try:
        fsops.remove_tree(ctx.work_dir)
    except OSError:
        pass
    ctx.store.update_task(ctx.task_id, work_path=None)


def run_task(store: Store, task_id: str, engines: dict, config: AidocConfig, emit: Emit | None = None,
             cancel: threading.Event | None = None, pause: threading.Event | None = None) -> TaskStatus:
    """Run one task to a terminal status (or back to `queued` when `pause` is set between segments).
    Unexpected exceptions end as failed(engine: internal error)."""
    if store.get_task(task_id) is None:
        raise KeyError(task_id)
    ctx = PipelineContext(store, task_id, config, emit, cancel, pause)
    lock = TaskLock(lock_path(store.db_path.parent, task_id))
    if not lock.acquire():                    # another process is running this very task: leave it alone
        ctx.log("task is being converted by another process; not started")
        return TaskStatus(store.get_task(task_id)["status"])
    status = None
    try:
        status = _run_task(ctx, engines)
        return status
    except KeyboardInterrupt:
        # Ctrl+C in the CLI: like a cancel (done segments kept, task resumable), then let the CLI exit
        try:
            status = _cancel(ctx)
        except Exception:  # noqa: BLE001, S110  best effort while exiting
            pass
        raise
    except Exception as e:  # noqa: BLE001  never leave a task stuck in probing/converting/checking
        ctx.log(traceback.format_exc())
        status = _fail(ctx, ErrorKind.engine, f"internal error: {type(e).__name__}: {e}")
        return status
    finally:
        if status in TERMINAL_TASK and (store.get_task(task_id) or {}).get("flags"):
            store.set_task_flags(task_id, {})        # index A18: `force` applies to one run only
        lock.release()


def _prepare_segments(ctx: PipelineContext, decision_engines: list[str]) -> list[str]:
    """Create or keep segment rows; returns the engine order (a resumable engine's done work is continued)."""
    store = ctx.store
    ranges = plan_segments(ctx.probe.pages if ctx.probe.kind == "pdf" else None)
    rows = store.list_segments(ctx.task_id)
    if [(r["page_start"], r["page_end"]) for r in rows] != ranges:
        if rows:
            _discard_segments(ctx)
            store.delete_segments(ctx.task_id)
        store.create_segments(ctx.task_id, ranges)
        return list(decision_engines)
    done_engines = set()
    key = options_key(ctx.opts)
    for r in rows:
        if r["status"] == SegmentStatus.done.value:
            part = load_segment_part(segment_dir(ctx.work_dir, r["idx"]), r)
            if part is not None and part.opts_key != key:
                ctx.log("options changed since the done segments were made (lang/OCR/tier); converting again")
                _discard_segments(ctx)
                return list(decision_engines)
            done_engines.add(part.engine if part is not None else None)
    if len(done_engines) == 1 and (eng := next(iter(done_engines))) in decision_engines:
        ctx.log(f"resuming with {eng}: {sum(r['status'] == 'done' for r in rows)} of {len(rows)} segments done")
        return decision_engines[decision_engines.index(eng):]
    if done_engines:
        _discard_segments(ctx)
    return list(decision_engines)


def _run_task(ctx: PipelineContext, engines: dict) -> TaskStatus:
    store, task_id = ctx.store, ctx.task_id
    task = store.get_task(task_id)
    job = store.get_job(task["job_id"])
    opts = ctx.opts = ConvertOptions.from_json(job["options"])
    ctx.attempt = task["attempt"]
    if ctx.cancelled():
        return _cancel(ctx)

    # 1. stage the sha-verified work copy (spec §8.5), probe, cache
    ctx.set(status=TaskStatus.probing, error_kind=None, error_msg=None)
    src = Path(task["source_path"])
    work_dir, _ = stage_paths(task_id)
    ctx.work_dir = work_dir
    # an upload task's source_path is a display name (never absolute): its file already lives in the work dir
    preferred = Path(task["work_path"]) if task.get("work_path") and not src.is_absolute() else None
    try:
        work_src = ctx.work_src = stage_source(src, task["sha256"], work_dir, preferred=preferred)
    except SourceError as e:
        return _fail(ctx, ErrorKind.input, f"{e.code}: {src}")
    ctx.set(work_path=str(work_src))
    probe = ctx.probe = probe_file(work_src)
    if probe.error:
        return _fail(ctx, ErrorKind.input, probe.error)
    output_dir = planned_output_dir(store, opts.output_dir, src, task["sha256"])
    _set_output_dir(store, task, output_dir)
    task = store.get_task(task_id)
    force = opts.force or bool((task.get("flags") or {}).get("force"))
    if not force:
        hit = lookup_cached(store, task["sha256"], output_dir)
        if hit is not None:
            level = (hit.get("quality") or {}).get("level") or hit.get("status")
            if level == "ok" or not opts.retry_low:
                ctx.set(status=TaskStatus.skipped, engine=hit.get("engine"), quality=hit.get("quality"))
                _drop_unused_work_dir(ctx)
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

    # 3. segment rows
    order = _prepare_segments(ctx, decision.engines)

    # 4. engines in order
    best: _Candidate | None = None
    for i, name in enumerate(order):
        if ctx.cancelled():
            return _cancel(ctx)
        engine = engines[name]
        eo = engine.engine_opts(opts, probe)
        last = i == len(order) - 1
        transient_used = 0
        result = None
        while result is None:                              # retries of this engine (spec §8.2)
            try:
                result = run_engine_attempt(ctx, engine, eo, quick_check=not last)
            except EngineCancelled:
                return _cancel(ctx)
            except TaskPaused:
                return _requeue_paused(ctx)
            except EngineError as e:
                d = RETRY_POLICY.decide(e, transient_used, engine, eo)
                msg = e.message
                if d.action == "retry" and d.note == "oom_downgrade":
                    changed = {k: v for k, v in d.engine_opts.items() if eo.get(k) != v}
                    msg = f"oom: retrying with {changed}; {e.message}"
                store.append_attempt(task_id, Attempt(engine=name, attempt=ctx.attempt, score=None, reasons=[],
                                                      error_kind=e.kind.value, error_msg=msg[:1000]))
                ctx.log(f"{name} failed ({e.kind.value}, {d.action}): {msg[:300]}")
                if d.action == "fail":
                    for seg in store.list_segments(task_id):      # the segment that hit the input error
                        if seg["status"] == SegmentStatus.converting.value:
                            store.update_segment(seg["id"], status=SegmentStatus.failed)
                            ctx.segment_updated(seg["id"])
                    return _fail(ctx, ErrorKind.input, f"{name}: {e.message[:500]}")
                if d.action == "fallback":
                    _discard_segments(ctx)
                    break
                if d.note != "oom_downgrade":
                    transient_used += 1
                eo = d.engine_opts if d.engine_opts is not None else eo
                if d.delay_s > 0:
                    if ctx.cancel is not None:
                        if ctx.cancel.wait(d.delay_s):
                            return _cancel(ctx)
                    else:
                        time.sleep(d.delay_s)
                if ctx.cancelled():
                    return _cancel(ctx)
        if result is None:                                 # fell back: next engine
            continue
        norm, q, page_count = result
        store.append_attempt(task_id, Attempt(engine=name, attempt=ctx.attempt, score=q.score, reasons=q.reasons))
        cand = _Candidate(name, norm, q, page_count)
        if q.level == "ok":
            return _write_output(ctx, task, cand, output_dir)
        ctx.log(f"{name} quality low ({q.score}): {', '.join(q.reasons)}")
        if best is None or q.score > best.quality.score:
            best = _keep_candidate(ctx, cand) if not last else cand
        if not last:
            _discard_segments(ctx)

    # 5. nothing passed
    if best is not None:
        return _write_output(ctx, task, best, output_dir)
    for seg in store.list_segments(task_id):
        store.update_segment(seg["id"], status=SegmentStatus.failed)
    return _fail(ctx, ErrorKind.engine, "all engines failed")
