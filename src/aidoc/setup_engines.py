"""`aidoc setup <engine>` (spec §9): uv sync, model download, self-check, .ready marker."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from aidoc import paths
from aidoc.config import AidocConfig
from aidoc.engines.host import runner_env
from aidoc.models import ENGINE_NAMES

GPU_ENGINES = ("docling", "mineru")


def _script(engine: str, name: str) -> Path:
    exe = f"{name}.exe" if sys.platform == "win32" else name
    return paths.venv_python(engine).parent / exe


def plan_commands(engine: str, config: AidocConfig) -> list[list[str]]:
    cmds: list[list[str]] = [["uv", "sync", "--project", str(paths.envs_dir() / engine)]]
    if engine == "docling":
        # both OCR engines are fetched so docling_ocr can be switched in aidoc.toml without re-running setup
        cmds.append([str(_script("docling", "docling-tools")), "models", "download",
                     "--output-dir", str(paths.models_dir() / "docling"),
                     "layout", "tableformer", "code_formula", "easyocr", "rapidocr",
                     "--easyocr-lang", "iso:zh-Hant", "--easyocr-lang", "en",
                     "--rapidocr-backend-lang", "onnxruntime:chinese_cht",
                     "--rapidocr-backend-lang", "onnxruntime:en"])
    elif engine == "mineru":
        cmds.append([str(_script("mineru", "mineru-kit")), "models", "download",
                     "--tier", config.engines.mineru_tier, "--small-backend", "torch", "--source", "auto"])
    cmds.append([str(paths.venv_python(engine)), str(paths.runner_dir() / f"selfcheck_{engine}.py")])
    return cmds


def check_long_paths() -> bool | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
            return winreg.QueryValueEx(k, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


def predownload_tiktoken(data_dir: Path) -> None:
    cache = Path(data_dir) / "tiktoken"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["TIKTOKEN_CACHE_DIR"] = str(cache)
    import tiktoken
    tiktoken.get_encoding("cl100k_base")


def _env(config: AidocConfig) -> dict[str, str]:
    env = {**os.environ, **runner_env(paths.data_dir()), "AIDOC_MINERU_TIER": config.engines.mineru_tier,
           "AIDOC_DOCLING_OCR": config.engines.docling_ocr}
    env.pop("VIRTUAL_ENV", None)          # uv sync --project must target envs/<e>/.venv, not the caller's venv
    return env


def _parse_selfcheck(stdout: str) -> dict | None:
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def setup(engine: str, config: AidocConfig, log: Callable[[str], None], run=subprocess.run) -> bool:
    if engine == "all":
        ok = True
        for e in ENGINE_NAMES:
            ok = setup(e, config, log, run=run) and ok
        return ok
    if engine not in ENGINE_NAMES:
        log(f"unknown engine {engine}")
        return False
    paths.models_dir().mkdir(parents=True, exist_ok=True)
    marker = paths.ready_marker(engine)
    if marker.exists():
        marker.unlink()
    env = _env(config)
    cmds = plan_commands(engine, config)
    result: dict | None = None
    for i, cmd in enumerate(cmds):
        log(f"[{engine}] $ {' '.join(cmd)}")
        r = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        for stream in (r.stdout, r.stderr):
            for line in (stream or "").splitlines():
                if line.strip():
                    log(f"[{engine}] {line}")
        if r.returncode != 0:
            log(f"[{engine}] command failed with exit code {r.returncode}")
            return False
        if i == len(cmds) - 1:
            result = _parse_selfcheck(r.stdout)
    if not result or not result.get("ok"):
        log(f"[{engine}] self-check did not report ok: {result}")
        return False
    if engine in GPU_ENGINES and not (result.get("cuda") is True and "sm_120" in (result.get("arch_list") or [])):
        log(f"[{engine}] GPU check failed: need torch.cuda.is_available() and sm_120 in get_arch_list(); "
            f"got cuda={result.get('cuda')} arch_list={result.get('arch_list')}")
        return False
    if int(result.get("sample_chars") or 0) < 20:
        log(f"[{engine}] self-check produced too little text: {result.get('sample_chars')}")
        return False
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"engine": engine, "checked_at": time.time(), "cuda": result.get("cuda"),
                                  "arch_list": result.get("arch_list") or [],
                                  "sample_chars": int(result.get("sample_chars") or 0),
                                  **{k: v for k, v in result.items() if k in ("torch", "version", "device")}},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    predownload_tiktoken(paths.data_dir())
    lp = check_long_paths()
    if lp is False:
        log("warning: Windows LongPathsEnabled is off; deep output paths may fail "
            "(enable HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem LongPathsEnabled=1)")
    log(f"ready: envs/{engine}/.ready written")
    return True
