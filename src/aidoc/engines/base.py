"""Engine interface (index §3) and the concrete RunnerEngine base used by all engines."""
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from aidoc import paths
from aidoc.models import ConvertOptions, ErrorKind, ProbeResult, ProgressCb, RawResult, TableEdge

if TYPE_CHECKING:
    from aidoc.config import AidocConfig


class EngineError(Exception):
    def __init__(self, kind: ErrorKind, message: str, *, oom: bool = False):
        super().__init__(message)
        self.kind = ErrorKind(kind)
        self.message = message
        self.oom = oom


class EngineCancelled(Exception):
    pass


class Engine(Protocol):
    name: str

    def available(self) -> bool: ...
    def supports(self, probe: ProbeResult) -> bool: ...
    def convert(self, src: Path, workdir: Path, opts: ConvertOptions,
                pages: tuple[int, int] | None, on_progress: ProgressCb) -> RawResult: ...


def raw_from_result(res: dict, raw_dir: Path) -> RawResult:
    md = Path(res["markdown_path"]).read_text(encoding="utf-8")
    ft, lt = res.get("first_table"), res.get("last_table")
    return RawResult(markdown=md, image_paths=[Path(p) for p in res.get("images", [])], raw_dir=raw_dir,
                     has_page_markers=bool(res.get("has_page_markers")), page_count=res.get("page_count"),
                     first_table=TableEdge.from_json(ft) if ft else None,
                     last_table=TableEdge.from_json(lt) if lt else None)


class RunnerEngine:
    """Engine backed by a runner script in envs/<name>/.venv (one-shot runner per convert in P1)."""
    name: str = ""

    def __init__(self, config: "AidocConfig | None" = None):
        if config is None:
            from aidoc.config import load_config
            config = load_config()
        self.config = config

    # --- runtime locations (overridden by FakeEngine)
    def python(self) -> Path:
        return paths.venv_python(self.name)

    def script(self) -> Path:
        return paths.runner_script(self.name)

    def env_extra(self) -> dict[str, str]:
        return {}

    # --- Engine API
    def available(self) -> bool:
        return self.python().exists() and paths.ready_marker(self.name).exists()

    def supports(self, probe: ProbeResult) -> bool:
        from aidoc.router import engine_supports
        return engine_supports(self.name, probe)

    def engine_opts(self, opts: ConvertOptions, probe: ProbeResult, full_page_ocr: bool = False) -> dict:
        return {}

    def oom_downgrade(self, engine_opts: dict) -> dict | None:
        return None

    def build_request(self, src: Path, workdir: Path, opts: ConvertOptions, probe: ProbeResult,
                      pages: tuple[int, int] | None, engine_opts: dict, segment_idx: int = 0) -> dict:
        return {"src": str(src), "out_dir": str(Path(workdir) / "raw"), "lang": opts.lang,
                "engine_opts": engine_opts, "kind": probe.kind,
                "pages": list(pages) if pages else None, "segment_idx": segment_idx}

    def convert(self, src: Path, workdir: Path, opts: ConvertOptions, pages: tuple[int, int] | None,
                on_progress: ProgressCb, *, timeout_s: float, probe: ProbeResult,
                engine_opts: dict | None = None, full_page_ocr: bool = False, segment_idx: int = 0,
                on_start=None) -> RawResult:
        from aidoc.engines.host import RunnerHost
        workdir = Path(workdir).resolve()
        src = Path(src).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        eo = engine_opts if engine_opts is not None else self.engine_opts(opts, probe, full_page_ocr)
        req = self.build_request(src, workdir, opts, probe, pages, eo, segment_idx)
        host = RunnerHost(self.python(), self.script(), self.env_extra(), self.config.limits.startup_timeout_s)
        host.start(workdir)
        if on_start is not None:
            on_start(host.pid)
        try:
            res = host.run(req, workdir, timeout_s, on_progress)
        finally:
            host.close()
        return raw_from_result(res, workdir / "raw")
