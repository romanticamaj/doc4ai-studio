"""Engine routing (spec §4, §13.2, §13.3)."""
from __future__ import annotations

from aidoc.models import ConvertOptions, ProbeResult, RouteDecision
from aidoc.probe import MARKITDOWN_EXTS

DOCLING_EXTS = frozenset({".docx", ".pptx", ".xlsx", ".html", ".htm", ".md", ".csv"})
_DOCLING_FALLBACK_EXTS = frozenset({".docx", ".pptx", ".html", ".htm"})   # spec §4 row 1


def engine_supports(engine: str, probe: ProbeResult) -> bool:
    if engine == "markitdown":
        return probe.ext in MARKITDOWN_EXTS or probe.ext == ".pdf"
    if engine == "docling":
        return probe.kind in ("pdf", "image") or probe.ext in DOCLING_EXTS
    if engine == "mineru":
        return probe.kind in ("pdf", "image")
    return False


def _rule(probe: ProbeResult, opts: ConvertOptions) -> tuple[list[str], str]:
    en = opts.lang == "en"
    if probe.kind != "pdf" and probe.kind != "image":
        if probe.ext not in MARKITDOWN_EXTS:
            return [], "unsupported_type"
        engines = ["markitdown"] + (["docling"] if probe.ext in _DOCLING_FALLBACK_EXTS else [])
        return engines, "non_pdf"
    if probe.kind == "image":
        return (["docling", "mineru"] if en else ["mineru", "docling"]), "image"
    if probe.text_ratio < 0.5 or probe.image_cover > 0.6:
        return (["docling", "mineru"] if en else ["mineru", "docling"]), "pdf_scanned"
    if probe.math_hint:
        return ["mineru", "docling"], "pdf_math"
    return ["docling", "mineru", "markitdown"], "pdf_default"


def route(probe: ProbeResult, opts: ConvertOptions, available: dict[str, bool]) -> RouteDecision:
    if probe.kind == "audio" and not opts.allow_online_audio:
        return RouteDecision(engines=[], reason="audio_disabled")
    if opts.engine:
        if not engine_supports(opts.engine, probe):
            return RouteDecision(engines=[], reason="engine_unsupported", forced=True)
        if not available.get(opts.engine, False):
            return RouteDecision(engines=[], reason="no_engine_available", forced=True, missing=[opts.engine])
        return RouteDecision(engines=[opts.engine], reason="forced", forced=True)
    engines, reason = _rule(probe, opts)
    if not engines:
        return RouteDecision(engines=[], reason=reason)
    ok = [e for e in engines if available.get(e, False)]
    missing = [e for e in engines if not available.get(e, False)]
    if not ok:
        return RouteDecision(engines=[], reason="no_engine_available", missing=missing)
    return RouteDecision(engines=ok, reason=reason, missing=missing)
