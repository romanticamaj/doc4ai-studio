"""aidoc.toml over the API (index A4, §7). Token is never readable nor writable here."""
from __future__ import annotations

import copy
from dataclasses import fields

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from aidoc.config import _SECTIONS, config_path, save_config
from aidoc.server.auth import ApiError

router = APIRouter()

_CHOICES = {("general", "lang"): {"cht", "en"}, ("engines", "mineru_tier"): {"basic", "standard"},
            ("engines", "docling_ocr"): {"easyocr", "rapidocr"}}
_MIN = {("general", "work_retention_days"): 0, ("engines", "docling_page_batch_size"): 1,
        ("server", "port"): 1, ("limits", "disk_space_factor"): 1, ("limits", "upload_max_bytes"): 1}
_MAX = {("server", "port"): 65535, ("limits", "upload_max_bytes"): 2 ** 40,        # 1 TiB
        ("engines", "docling_page_batch_size"): 1024, ("limits", "disk_space_factor"): 100000,
        ("general", "work_retention_days"): 3650}
_INT_MAX = 10 ** 9                      # any other int (timeouts in seconds): ~30 years is already absurd


def masked(ctx) -> dict:
    d = ctx.config.to_dict()
    if d["server"].get("token") or ctx.token:
        d["server"]["token"] = "***"
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
    return clean


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
    await run_in_threadpool(save_config, new, config_path())
    for section, values in clean.items():             # hot-apply to the running server
        for key, val in values.items():
            setattr(getattr(ctx.config, section), key, val)
    ctx.bus.publish("system.updated", None, {"settings_changed": sorted(clean)})
    return {"settings": masked(ctx)}
