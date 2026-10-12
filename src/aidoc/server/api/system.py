"""System status and engine setup (index §8, spec §8.8, §9)."""
from __future__ import annotations

import json
import shutil

from fastapi import APIRouter, Request

from aidoc import __version__, gpu, paths
from aidoc.models import ENGINE_NAMES
from aidoc.preview import soffice_info
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


def vlm_status(ctx) -> dict:
    """engines.vlm (VLM spec §10.1): the usual {installed, ready, checked_at} plus backend, remote, endpoint, model,
    budget, roles and local_supported. "installed" = a backend is configured; "ready" = usable right now."""
    from aidoc.engines.vlm import VlmEngine
    v = ctx.config.vlm
    eng = VlmEngine(ctx.config)
    configured = v.backend == "openai" and bool(v.base_url and v.model)
    out = {"installed": configured, "ready": eng.available(), "backend": v.backend,
           "remote": v.backend == "openai", "remote_enabled": bool(v.remote_enabled and v.backend == "openai"),
           "endpoint_host": eng.endpoint_host() if v.backend == "openai" else None,
           "model": eng.model_name() or None,
           "local_endpoint": eng.local_endpoint() if v.backend == "openai" else None,
           "key_set": eng.key_available() if v.backend == "openai" else None,
           "budget": None, "roles": {"fallback": bool(v.use_as_fallback), "repair": bool(v.use_for_repair)},
           "local_supported": eng.local_supported()}
    if v.backend == "openai" and v.base_url:
        today = ctx.store.vlm_usage_today("openai", eng.endpoint_host())
        out["budget"] = {"used_today": today["pages"], "requests_today": today["requests"],
                         "limit": v.remote_daily_page_budget}
    marker = paths.ready_marker("vlm")
    if marker.exists():
        try:
            info = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        if info.get("backend") == v.backend and info.get("model") == eng.model_name():
            out["checked_at"] = info.get("checked_at")       # the last connection test of this endpoint/model
    return out


def system_snapshot(ctx) -> dict:
    out_root = ctx.config.output_root()
    probe = out_root if out_root.exists() else ctx.config.root
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        free = None
    engines = {n: (vlm_status(ctx) if n == "vlm" else engine_status(n)) for n in ENGINE_NAMES}
    return {"engines": engines, "gpu": gpu.query(),
            "queue": ctx.queue.snapshot(), "disk_free": free, "output_dir": str(out_root), "version": __version__,
            "long_paths_enabled": check_long_paths(), "libreoffice": soffice_info(ctx.config.preview.soffice_path)}


@router.get("/system")
def get_system(request: Request) -> dict:
    return system_snapshot(request.app.state.ctx)


@router.post("/system/vlm/check", status_code=202)
def run_vlm_check(request: Request) -> dict:
    """The VLM connection test (VLM spec §9/§10.1): no install, progress as setup.log / setup.done (engine "vlm")."""
    ctx = request.app.state.ctx
    runner = ctx.extras.get("setup")
    if runner is None:
        runner = ctx.extras.setdefault("setup", SetupRunner(ctx))
    try:
        return runner.start("vlm", action="check")
    except SetupBusy:
        raise ApiError(409, "setup_running", **(runner.current or {})) from None


@router.get("/system/vlm/usage")
def get_vlm_usage(request: Request, days: int = 30) -> dict:
    ctx = request.app.state.ctx
    days = max(1, min(int(days), 366))
    rows = ctx.store.vlm_usage_days(days)
    today = ctx.store._today()
    agg = {"day": today, "pages": 0, "requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
    for r in rows:
        if r["day"] == today:
            for k in ("pages", "requests", "prompt_tokens", "completion_tokens"):
                agg[k] += int(r[k] or 0)
    return {"days": rows, "today": agg}


@router.post("/system/setup/{engine}", status_code=202)
def run_setup(engine: str, request: Request) -> dict:
    ctx = request.app.state.ctx
    if engine not in (*ENGINE_NAMES, "all"):
        raise ApiError(404, "unknown_engine", engine=engine)
    busy = ctx.queue.busy_engine(engine)
    if busy is not None:                        # never rebuild an env a runner is using (P3 final review)
        raise ApiError(409, "engine_busy", engine=busy, task_id=ctx.queue.running_task_id)
    runner = ctx.extras.get("setup")
    if runner is None:
        runner = ctx.extras.setdefault("setup", SetupRunner(ctx))
    try:
        return runner.start(engine)
    except SetupBusy:
        raise ApiError(409, "setup_running", **(runner.current or {})) from None
