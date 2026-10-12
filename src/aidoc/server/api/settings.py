"""aidoc.toml over the API (index A4, §7). Token is never readable nor writable here."""
from __future__ import annotations

import copy
from dataclasses import fields
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from aidoc.config import _SECTIONS, VLM_RANGES, config_path, mcp_list_problem, save_config, vlm_problem
from aidoc.server.auth import ApiError

router = APIRouter()

_CHOICES = {("general", "lang"): {"cht", "en"}, ("engines", "mineru_tier"): {"basic", "standard"},
            ("engines", "docling_ocr"): {"easyocr", "rapidocr"}, ("compat", "json_content"): {"null", "doc4ai"}}
_MIN = {("general", "work_retention_days"): 0, ("engines", "docling_page_batch_size"): 1,
        ("server", "port"): 1, ("limits", "disk_space_factor"): 1, ("limits", "upload_max_bytes"): 1}
_MAX = {("server", "port"): 65535, ("limits", "upload_max_bytes"): 2 ** 40,        # 1 TiB
        ("engines", "docling_page_batch_size"): 1024, ("limits", "disk_space_factor"): 100000,
        ("general", "work_retention_days"): 3650}
_MIN.update({("preview", "timeout_s"): 10, ("preview", "max_mb"): 1})
_MAX.update({("preview", "timeout_s"): 3600, ("preview", "max_mb"): 2048})
_INT_MAX = 10 ** 9                      # any other int (timeouts in seconds): ~30 years is already absurd

# [mcp] (MCP spec §10, plan index §4 §7)
_MIN.update({("mcp", "default_token_ttl_days"): 1, ("mcp", "max_token_ttl_days"): 1, ("mcp", "rate_limit_per_min"): 1,
             ("mcp", "max_concurrent_jobs_per_token"): 1, ("mcp", "max_upload_mb"): 1,
             ("mcp", "response_token_budget"): 500, ("mcp", "call_log_max_rows"): 1000,
             ("mcp", "call_log_retention_days"): 1})          # 0 would delete the whole call log every run
_MAX.update({("mcp", "max_upload_mb"): 2048, ("mcp", "response_token_budget"): 25000,
             ("mcp", "max_token_ttl_days"): 3650, ("mcp", "default_token_ttl_days"): 3650})


# [limits] archive and engine memory caps (verifier round, item 1)
_MIN.update({("limits", "archive_max_uncompressed_mb"): 1, ("limits", "archive_max_entries"): 1,
             ("limits", "archive_max_entry_mb"): 1, ("limits", "archive_max_ratio"): 2})
_MAX.update({("limits", "archive_max_uncompressed_mb"): 65536, ("limits", "archive_max_entries"): 1000000,
             ("limits", "archive_max_entry_mb"): 65536, ("limits", "archive_max_ratio"): 100000,
             ("limits", "engine_memory_mb"): 1048576, ("limits", "markitdown_memory_mb"): 1048576})

# [compat] (compat spec §7, plan §4)
_MIN.update({("compat", "sync_timeout_s"): 10, ("compat", "max_file_mb"): 1, ("compat", "max_source_mb"): 1,
             ("compat", "embed_images_max_mb"): 1, ("compat", "max_concurrent_jobs_per_token"): 1,
             ("compat", "poll_max_wait_s"): 1, ("compat", "task_retention_days"): 1})
_MAX.update({("compat", "sync_timeout_s"): 3600, ("compat", "max_file_mb"): 4096, ("compat", "max_source_mb"): 1024,
             ("compat", "embed_images_max_mb"): 1024, ("compat", "max_concurrent_jobs_per_token"): 100,
             ("compat", "poll_max_wait_s"): 300, ("compat", "task_retention_days"): 3650})


# [vlm] (VLM spec §8.3)
_CHOICES.update({("vlm", "backend"): {"none", "local", "openai"},
                 ("vlm", "local_preset"): {"paddleocr-vl-1.6", "qwen2.5-vl-3b", "custom"},
                 ("vlm", "local_prompt_style"): {"", "paddle_tasks", "generic"}})
_MIN.update({("vlm", k): lo for k, (lo, _hi) in VLM_RANGES.items()})
_MAX.update({("vlm", k): hi for k, (_lo, hi) in VLM_RANGES.items()})


def _validate_mcp_lists(key: str, val, allow_drive_root: bool = False) -> None:
    if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
        raise ApiError(422, "invalid_settings", detail=f"mcp.{key} must be a list of strings")
    for v in val:
        problem = mcp_list_problem(key, v, allow_drive_root)   # the same rules aidoc.toml is checked with at load
        if problem is not None:
            raise ApiError(422, "invalid_settings", detail=problem[0])


def masked(ctx) -> dict:
    d = ctx.config.to_dict()
    if d["server"].get("token") or ctx.token:
        d["server"]["token"] = "***"
    if d["vlm"].get("api_key"):
        d["vlm"]["api_key"] = "***"
    return d


def _validate(body: dict, cfg) -> dict:
    if not isinstance(body, dict):
        raise ApiError(422, "invalid_settings", detail="body must be an object of sections")
    clean: dict = {}
    for section, values in body.items():
        if section not in _SECTIONS or not isinstance(values, dict):
            raise ApiError(422, "invalid_settings", detail=f"unknown section {section!r}")
        known = {f.name for f in fields(_SECTIONS[section])}
        for key, val in values.items():
            if key not in known:
                raise ApiError(422, "invalid_settings", detail=f"unknown key {section}.{key}")
            cur = getattr(getattr(cfg, section), key)
            if (section, key) == ("server", "token"):
                if val in ("***", cur):              # the masked or unchanged value a settings form echoes back
                    continue
                raise ApiError(403, "token_readonly")
            if (section, key) == ("vlm", "api_key") and val == "***":
                continue                             # the masked value a settings form echoes back: unchanged
            if (section, key) == ("preview", "soffice_path"):
                if val == cur:
                    continue                         # a settings form echoes the value back
                # an executable path set over the API would let any token holder run any program on the host
                raise ApiError(403, "preview_path_readonly", detail="set preview.soffice_path in aidoc.toml")
            if section == "mcp" and key in ("allowed_hosts", "local_path_roots"):
                allow = values.get("allow_drive_root", cfg.mcp.allow_drive_root)
                _validate_mcp_lists(key, val, allow is True)
                clean.setdefault(section, {})[key] = val
                continue
            ok = type(val) is type(cur) or (type(cur) is int and type(val) is int)
            if not ok:
                raise ApiError(422, "invalid_settings", detail=f"{section}.{key} must be {type(cur).__name__}")
            if (section, key) in _CHOICES and val not in _CHOICES[(section, key)]:
                raise ApiError(422, "invalid_settings",
                               detail=f"{section}.{key} must be one of {sorted(_CHOICES[(section, key)])}")
            lo = _MIN.get((section, key), 0 if isinstance(val, int) and not isinstance(val, bool) else None)
            if lo is not None and val < lo:
                raise ApiError(422, "invalid_settings", detail=f"{section}.{key} must be >= {lo}")
            hi = _MAX.get((section, key), _INT_MAX if isinstance(val, int) and not isinstance(val, bool) else None)
            if hi is not None and val > hi:
                raise ApiError(422, "invalid_settings", detail=f"{section}.{key} must be <= {hi}")
            if isinstance(val, str) and key == "output_dir":
                if not val.strip():
                    raise ApiError(422, "invalid_settings", detail="general.output_dir must not be empty")
                if val.replace("\\", "/").startswith("//"):   # UNC: a network share (and an NTLM handshake)
                    raise ApiError(422, "invalid_settings", detail="general.output_dir must be a local path")
            if section == "server":
                if val == cur:
                    continue
                raise ApiError(403, "server_readonly", detail=f"{section}.{key} is set when aidoc serve starts")
            clean.setdefault(section, {})[key] = val
    if "vlm" in clean:
        _validate_vlm(clean["vlm"], cfg)
    if "mcp" in clean:
        m = clean["mcp"]
        default_ttl = m.get("default_token_ttl_days", cfg.mcp.default_token_ttl_days)
        max_ttl = m.get("max_token_ttl_days", cfg.mcp.max_token_ttl_days)
        if default_ttl > max_ttl:
            raise ApiError(422, "invalid_settings", detail="mcp.default_token_ttl_days must be <= mcp.max_token_ttl_days")
    return clean


def _validate_vlm(changes: dict, cfg) -> None:
    """The merged [vlm] section must pass the same rules aidoc.toml is loaded with, plus the API-only ones."""
    v = copy.deepcopy(cfg.vlm)
    for key, val in changes.items():
        setattr(v, key, val)
    from urllib.parse import urlsplit
    if "base_url" in changes and v.remote_enabled and "remote_enabled" not in changes \
            and urlsplit(changes["base_url"]).netloc.lower() != urlsplit(cfg.vlm.base_url).netloc.lower():
        # consent named the old host (spec §4.6): a new host needs it again
        v.remote_enabled = False
        changes["remote_enabled"] = False
    problem = vlm_problem(v)
    if problem:
        raise ApiError(422, "invalid_settings", detail=problem)
    if v.remote_enabled and v.backend != "openai":
        raise ApiError(422, "invalid_settings", detail="vlm.remote_enabled requires vlm.backend = \"openai\"")
    if v.backend == "local" and v.local_preset == "custom":
        for key in ("local_gguf", "local_mmproj"):
            val = getattr(v, key)
            if not val or not Path(val).is_absolute() or not Path(val).is_file():
                raise ApiError(422, "invalid_settings", detail=f"vlm.{key} must be an existing absolute path")


@router.get("/settings")
def get_settings(request: Request) -> dict:
    return {"settings": masked(request.app.state.ctx)}


@router.put("/settings")
async def put_settings(request: Request) -> dict:
    ctx = request.app.state.ctx
    try:
        body = await request.json()
    except (ValueError, RecursionError):
        raise ApiError(422, "invalid_settings", detail="body is not JSON") from None
    clean = _validate(body, ctx.config)
    new = copy.deepcopy(ctx.config)
    for section, values in clean.items():
        for key, val in values.items():
            setattr(getattr(new, section), key, val)
    changes = {sec: {k: v for k, v in vals.items() if getattr(getattr(ctx.config, sec), k) != v}
               for sec, vals in clean.items()}
    changes = {sec: vals for sec, vals in changes.items() if vals}
    if changes:                                      # a no-op save must not rewrite the file (M4)
        await run_in_threadpool(save_config, new, config_path(), changes)
    for section, values in clean.items():             # hot-apply to the running server
        for key, val in values.items():
            setattr(getattr(ctx.config, section), key, val)
    ctx.bus.publish("system.updated", None, {"settings_changed": sorted(clean)})
    return {"settings": masked(ctx)}
