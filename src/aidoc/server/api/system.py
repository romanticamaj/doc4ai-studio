"""System status and engine setup (index §8, spec §8.8, §9)."""
from __future__ import annotations

import json
import shutil

from fastapi import APIRouter, Request

from aidoc import __version__, gpu, paths
from aidoc.models import ENGINE_NAMES
from aidoc.server.auth import ApiError
from aidoc.server.setup_runner import SetupBusy, SetupRunner
from aidoc.setup_engines import check_long_paths

router = APIRouter()


def engine_status(name: str) -> dict:
    out = {"installed": paths.venv_python(name).exists(), "ready": False}
    marker = paths.ready_marker(name)
    if out["installed"] and marker.exists():
        out["ready"] = True
        try:
            info = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        for k in ("checked_at", "cuda", "version", "torch", "device"):
            if k in info:
                out[k] = info[k]
    return out


def system_snapshot(ctx) -> dict:
    out_root = ctx.config.output_root()
    probe = out_root if out_root.exists() else ctx.config.root
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        free = None
    return {"engines": {n: engine_status(n) for n in ENGINE_NAMES}, "gpu": gpu.query(),
            "queue": ctx.queue.snapshot(), "disk_free": free, "output_dir": str(out_root), "version": __version__,
            "long_paths_enabled": check_long_paths()}


@router.get("/system")
def get_system(request: Request) -> dict:
    return system_snapshot(request.app.state.ctx)


@router.post("/system/setup/{engine}", status_code=202)
def run_setup(engine: str, request: Request) -> dict:
    ctx = request.app.state.ctx
    if engine not in (*ENGINE_NAMES, "all"):
        raise ApiError(404, "unknown_engine", engine=engine)
    runner = ctx.extras.get("setup")
    if runner is None:
        runner = ctx.extras.setdefault("setup", SetupRunner(ctx))
    try:
        return runner.start(engine)
    except SetupBusy:
        raise ApiError(409, "setup_running", **(runner.current or {})) from None
