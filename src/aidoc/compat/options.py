"""docling-serve options → Doc4AI conversion options (compat spec §4.2, plan D10).

Lenient by design: options we do not implement are ignored and listed in `ignored_options` (docling-serve ignores
unknown form fields too); only values we would otherwise misinterpret are refused with 422."""
from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from aidoc import paths
from aidoc.compat.auth import CompatError
from aidoc.models import ENGINE_NAMES

MAX_TIMEOUT_S = 10_000_000
PRODUCED_FORMATS = ("md", "text")
IMAGE_MODES = ("placeholder", "referenced", "embedded")
LANG_MAP = {"zh-hant": "cht", "zh-tw": "cht", "zh": "cht", "cht": "cht", "zh-hk": "cht", "chinese_cht": "cht",
            "en": "en", "en-us": "en", "en-gb": "en", "eng": "en"}
_HANDLED = {"to_formats", "md_page_break_placeholder", "image_export_mode", "ocr_lang", "force_ocr", "document_timeout",
            "doc4ai_engine", "doc4ai_lang", "doc4ai_force", "doc4ai_keep_page_markers", "target", "target_type"}
_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off", ""}


@dataclass
class CompatOptions:
    to_formats: list[str]                 # produced formats: "md" always, "text" when asked
    unsupported_formats: list[str]
    placeholder: str                      # md_page_break_placeholder ("" = none)
    keep_page_markers: bool
    image_mode: str                       # placeholder | referenced | embedded
    lang: str                             # cht | en
    engine: str | None
    force: bool
    timeout_s: int | None
    warnings: list[str] = field(default_factory=list)
    ignored_options: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> CompatOptions:
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


def _invalid(key: str, msg: str):
    return CompatError(422, [{"type": "value_error", "loc": ["options", key], "msg": msg}])


def _scalar(raw: Mapping, key: str):
    v = raw[key]
    if isinstance(v, list) and len(v) == 1:            # a multipart field sent once, collected as a list
        v = v[0]
    return v


def _bool(raw: Mapping, key: str) -> bool:
    v = _scalar(raw, key)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, str)) and str(v).strip().lower() in _TRUE | _FALSE:
        return str(v).strip().lower() in _TRUE
    raise _invalid(key, "must be a boolean (true/false)")


def _list(raw: Mapping, key: str) -> list[str]:
    v = raw[key]
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise _invalid(key, "must be a list of strings")
    out: list[str] = []
    for item in v:                                      # repeated fields and comma-separated values alike
        out.extend(p.strip() for p in item.split(",") if p.strip())
    return out


def _str(raw: Mapping, key: str) -> str:
    v = _scalar(raw, key)
    if not isinstance(v, str):
        raise _invalid(key, "must be a string")
    return v


def _choice(raw: Mapping, key: str, choices) -> str:
    v = _str(raw, key).strip()
    if v not in choices:
        raise _invalid(key, f"must be one of {', '.join(choices)}")
    return v


def parse_options(raw: Mapping[str, Any], cfg, *, multipart: bool) -> CompatOptions:
    """`raw` is the JSON `options` object, or the multipart form fields (a value per field, or a list when the field
    was repeated). Raises CompatError(422) for values we cannot honor."""
    if not isinstance(raw, Mapping):
        raise _invalid("options", "must be an object")
    warnings: list[str] = []
    ignored: set[str] = set()
    requested = _list(raw, "to_formats") if "to_formats" in raw else ["md"]
    produced = ["md"] + (["text"] if "text" in requested else [])
    unsupported = list(dict.fromkeys(f for f in requested if f not in PRODUCED_FORMATS))
    placeholder = _str(raw, "md_page_break_placeholder") if "md_page_break_placeholder" in raw else ""
    image_mode = _choice(raw, "image_export_mode", IMAGE_MODES) if "image_export_mode" in raw else "placeholder"
    lang = cfg.general.lang
    if "ocr_lang" in raw:
        for code in _list(raw, "ocr_lang"):
            mapped = LANG_MAP.get(code.lower())
            if mapped == "cht":
                lang = "cht"
                break
            if mapped == "en":
                lang = "en"
    if "doc4ai_lang" in raw:
        lang = _choice(raw, "doc4ai_lang", ("cht", "en"))
    engine = _choice(raw, "doc4ai_engine", ENGINE_NAMES) if "doc4ai_engine" in raw else None
    if "force_ocr" in raw and _bool(raw, "force_ocr") and engine is None:
        if paths.ready_marker("mineru").exists():
            engine = "mineru"                           # full-page OCR, no fallback (spec §4.2)
        else:
            warnings.append("force_ocr ignored: MinerU (full-page OCR) is not installed; the router decides")
    timeout_s = None
    if "document_timeout" in raw:
        v = _scalar(raw, "document_timeout")
        try:
            secs = float(v)
        except (TypeError, ValueError, OverflowError):
            raise _invalid("document_timeout", "must be a number of seconds") from None
        if not math.isfinite(secs) or not 1 <= secs <= MAX_TIMEOUT_S:      # inf, nan, 1e400, negative (verifier 4)
            raise _invalid("document_timeout", f"must be between 1 and {MAX_TIMEOUT_S} seconds")
        timeout_s = max(1, min(int(secs), cfg.compat.sync_timeout_s))
    for key in ("target_type", "target"):
        if key in raw:
            v = _scalar(raw, key)
            kind = v.get("kind") if isinstance(v, dict) else v
            if kind not in (None, "inbody"):
                raise CompatError(422, f"unsupported_target: {kind}")
    for key in raw:
        if key in _HANDLED:
            continue
        ignored.add(key)
        if key == "do_ocr" and not _bool(raw, key):
            warnings.append("do_ocr=false ignored: Doc4AI's router decides when to OCR")
        elif key == "page_range":
            warnings.append("page_range ignored: the whole document is converted")
        elif key == "do_chunking" and _truthy(raw[key]):
            warnings.append("do_chunking ignored: no docling chunks; split md_content yourself")
    return CompatOptions(to_formats=produced, unsupported_formats=unsupported, placeholder=placeholder,
                         keep_page_markers=_bool(raw, "doc4ai_keep_page_markers") if "doc4ai_keep_page_markers" in raw else False,
                         image_mode=image_mode, lang=lang, engine=engine,
                         force=_bool(raw, "doc4ai_force") if "doc4ai_force" in raw else False,
                         timeout_s=timeout_s, warnings=warnings, ignored_options=sorted(ignored))


def _truthy(v) -> bool:
    if isinstance(v, list) and v:
        v = v[0]
    return v is True or (isinstance(v, (str, int)) and str(v).strip().lower() in _TRUE)
