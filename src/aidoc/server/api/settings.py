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
        ("server", "port"): 1}


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
            if (section, key) == ("server", "token"):
                raise ApiError(403, "token_readonly")
            cur = getattr(getattr(cfg, section), key)
            ok = type(val) is type(cur) or (type(cur) is int and type(val) is int)
            if not ok:
                raise ApiError(422, "invalid_settings", detail=f"{section}.{key} must be {type(cur).__name__}")
            if (section, key) in _CHOICES and val not in _CHOICES[(section, key)]:
                raise ApiError(422, "invalid_settings",
                               detail=f"{section}.{key} must be one of {sorted(_CHOICES[(section, key)])}")
            lo = _MIN.get((section, key), 0 if isinstance(val, int) and not isinstance(val, bool) else None)
            if lo is not None and val < lo:
                raise ApiError(422, "invalid_settings", detail=f"{section}.{key} must be >= {lo}")
            if isinstance(val, str) and key == "output_dir" and not val.strip():
                raise ApiError(422, "invalid_settings", detail="general.output_dir must not be empty")
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
    except ValueError:
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
