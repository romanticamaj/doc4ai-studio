"""RunnerHost: spawn an engine runner and speak the index §5 protocol over stdin/stderr."""
from __future__ import annotations

import collections
import io
import json
import math
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

from aidoc import paths, procs
from aidoc.engines.base import EngineCancelled, EngineError
from aidoc.models import ConvertOptions, ErrorKind, ProgressCb

_PREFIXES = ("AIDOC_READY", "AIDOC_PROGRESS", "AIDOC_DONE", "AIDOC_ERROR")


def runner_env(data_dir: Path) -> dict[str, str]:
    env_models = os.environ.get("AIDOC_MODELS")
    models = Path(env_models) if env_models else Path(data_dir) / "models"
    return {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "MINERU_HOME": str(models / "mineru"),
        "MINERU_MODEL_SMALL_BACKEND": "torch",
        "HF_HOME": str(models / "hf"),
        "EASYOCR_MODULE_PATH": str(models / "easyocr"),
        "AIDOC_MODELS_DIR": str(models),
    }


def compute_timeout(pages: int | None, size_bytes: int, opts: ConvertOptions, limits) -> float:
    if opts.timeout_s:
        return opts.timeout_s
    if pages:
        return max(limits.timeout_min_s, pages * limits.timeout_per_page_s)
    mb = math.ceil(size_bytes / 2**20)
    return min(limits.timeout_max_no_pages_s, max(limits.timeout_min_s, mb * limits.timeout_per_mb_s))


def _parse(line: str) -> tuple[str | None, dict]:
    for p in _PREFIXES:
        if line.startswith(p + " ") or line.rstrip("\r\n") == p:
            rest = line[len(p):].strip()
            try:
                return p[len("AIDOC_"):], (json.loads(rest) if rest else {})
            except json.JSONDecodeError:
                return None, {}
    return None, {}


class RunnerHost:
    def __init__(self, python: Path, script: Path, env_extra: dict[str, str], startup_timeout_s: float = 900.0):
        self.python, self.script = Path(python), Path(script)
        self.env_extra = dict(env_extra)
        self.startup_timeout_s = startup_timeout_s
        self.proc: subprocess.Popen | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._tail: collections.deque[str] = collections.deque(maxlen=200)
        self._pump_thread: threading.Thread | None = None
        self.ready = False
        self.pid: int | None = None

    def start(self, cwd: Path) -> None:
        cwd = Path(cwd).resolve()
        cwd.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, **runner_env(paths.data_dir()), **self.env_extra}
        self.proc = subprocess.Popen([str(self.python), str(self.script)], cwd=str(cwd), env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     **procs.popen_kwargs())
        self.pid = self.proc.pid
        self._pump_thread = threading.Thread(target=self._pump, daemon=True)
        self._pump_thread.start()

    def _pump(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        with io.TextIOWrapper(self.proc.stderr, encoding="utf-8", errors="replace") as f:
            for line in f:
                self._tail.append(line)
                self._lines.put(line)
        self._lines.put(None)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _next_line(self, timeout: float = 0.25) -> str | None:
        """'' = nothing yet; None = stream closed."""
        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            return ""

    def _raise_dead(self, why: str):
        if self._pump_thread is not None:
            self._pump_thread.join(timeout=5)          # make sure the stderr tail is complete
        if self.proc is not None:
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        tail = "".join(self._tail)
        oom = ("CUDA out of memory" in tail) or ("OutOfMemoryError" in tail)
        code = self.proc.returncode if self.proc else None
        raise EngineError(ErrorKind.transient, f"{why} (code {code}): {tail[-500:]}", oom=oom)

    def _check_cancel(self, cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            self.kill()
            raise EngineCancelled("cancelled")

    def _wait_ready(self, on_progress: ProgressCb, cancel: threading.Event | None = None) -> None:
        if self.ready:
            return
        deadline = time.monotonic() + self.startup_timeout_s
        while True:
            self._check_cancel(cancel)
            line = self._next_line()
            if line is None or (line == "" and not self.alive() and self._lines.empty()):
                self._raise_dead("runner exited during startup")
            if line:
                kind, _ = _parse(line)
                if kind in ("READY", "PROGRESS"):
                    self.ready = True
                    return
                if kind is None:
                    on_progress(-1.0, line.rstrip("\r\n"))
            if time.monotonic() > deadline:
                self.kill()
                raise EngineError(ErrorKind.transient, f"startup timeout after {self.startup_timeout_s:.0f}s")

    def run(self, request: dict, workdir: Path, timeout_s: float, on_progress: ProgressCb,
            cancel: threading.Event | None = None) -> dict:
        """Send one request and wait for its result. `cancel` is polled every 0.25 s (set -> kill, EngineCancelled)."""
        if self.proc is None:
            raise RuntimeError("RunnerHost.start() not called")
        workdir = Path(workdir).resolve()          # runner cwd is the workdir: never hand it a relative path
        workdir.mkdir(parents=True, exist_ok=True)
        req_path = workdir / "request.json"
        req_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        self._wait_ready(on_progress, cancel)
        self._check_cancel(cancel)
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write((str(req_path) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except OSError:
            self._raise_dead("runner stdin closed")
        deadline = time.monotonic() + timeout_s
        while True:
            self._check_cancel(cancel)
            line = self._next_line()
            if line is None or (line == "" and not self.alive() and self._lines.empty()):
                self._check_cancel(cancel)
                self._raise_dead("runner exited")
            if time.monotonic() > deadline:
                self.kill()
                raise EngineError(ErrorKind.transient, f"timeout after {timeout_s:.0f}s")
            if not line:
                continue
            kind, payload = _parse(line)
            if kind == "PROGRESS":
                total = max(1, int(payload.get("total") or 1))
                on_progress(min(1.0, int(payload.get("page") or 0) / total), "")
            elif kind == "DONE":
                return json.loads(Path(payload["result"]).read_text(encoding="utf-8"))
            elif kind == "ERROR":
                kind_s, msg = str(payload.get("kind", "engine")), str(payload.get("message", ""))
                try:
                    kind_e = ErrorKind(kind_s)
                except ValueError:                   # unknown kind from a runner: treat as an engine failure
                    kind_e, msg = ErrorKind.engine, f"[runner error kind {kind_s!r}] {msg}"
                raise EngineError(kind_e, msg)
            elif kind is None:
                on_progress(-1.0, line.rstrip("\r\n"))   # log line, fraction -1 means "no progress info"

    def kill(self) -> None:
        if self.proc is None:
            return
        procs.kill_tree(self.proc.pid)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.kill()
