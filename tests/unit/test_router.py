from pathlib import Path

import pytest

from aidoc.models import ConvertOptions, ProbeResult
from aidoc.router import engine_supports, route

ALL = {"markitdown": True, "docling": True, "mineru": True}


def opts(**kw):
    return ConvertOptions(output_dir=Path("out"), **kw)


def pdf(**kw):
    return ProbeResult(kind="pdf", ext=".pdf", size=1, pages=3, **kw)


@pytest.mark.parametrize("probe,expected,reason", [
    (ProbeResult(kind="office", ext=".docx", size=1), ["markitdown", "docling"], "non_pdf"),
    (ProbeResult(kind="office", ext=".xlsx", size=1), ["markitdown"], "non_pdf"),
    (ProbeResult(kind="html", ext=".html", size=1), ["markitdown", "docling"], "non_pdf"),
    (ProbeResult(kind="other", ext=".epub", size=1), ["markitdown"], "non_pdf"),
    (ProbeResult(kind="image", ext=".png", size=1), ["mineru", "docling"], "image"),
    (pdf(text_ratio=0.2), ["mineru", "docling"], "pdf_scanned"),
    (pdf(text_ratio=1.0, image_cover=0.7), ["mineru", "docling"], "pdf_scanned"),
    (pdf(text_ratio=1.0, math_hint=True), ["mineru", "docling"], "pdf_math"),
    (pdf(text_ratio=1.0), ["docling", "mineru", "markitdown"], "pdf_default"),
])
def test_rules(probe, expected, reason):
    d = route(probe, opts(), ALL)
    assert d.engines == expected and d.reason == reason


def test_lang_en_scanned_prefers_docling_only_with_an_english_ocr_model():
    """§13.2 (revised in P2): Docling first for English scans only when its OCR has an English model (EasyOCR `en`).
    RapidOCR (default, spike B) loads the same multilingual PP-OCRv6 model for `en` and `chinese_cht`."""
    en_easy = opts(lang="en", docling_ocr="easyocr")
    assert route(pdf(text_ratio=0.0), en_easy, ALL).engines == ["docling", "mineru"]
    assert route(ProbeResult(kind="image", ext=".png", size=1), en_easy, ALL).engines == ["docling", "mineru"]
    assert route(pdf(text_ratio=1.0, math_hint=True), en_easy, ALL).engines == ["mineru", "docling"]
    en_rapid = opts(lang="en", docling_ocr="rapidocr")
    assert route(pdf(text_ratio=0.0), en_rapid, ALL).engines == ["mineru", "docling"]
    assert route(ProbeResult(kind="image", ext=".png", size=1), en_rapid, ALL).engines == ["mineru", "docling"]


def test_forced_engine_no_fallback():
    d = route(pdf(text_ratio=1.0), opts(engine="mineru"), ALL)
    assert d.engines == ["mineru"] and d.forced
    d = route(ProbeResult(kind="office", ext=".docx", size=1), opts(engine="mineru"), ALL)
    assert d.engines == [] and d.reason == "engine_unsupported"


def test_audio_gate():
    a = ProbeResult(kind="audio", ext=".mp3", size=1)
    assert route(a, opts(), ALL).reason == "audio_disabled"
    assert route(a, opts(allow_online_audio=True), ALL).engines == ["markitdown"]


def test_unsupported_and_unavailable():
    assert route(ProbeResult(kind="other", ext=".exe", size=1), opts(), ALL).reason == "unsupported_type"
    d = route(pdf(text_ratio=0.0), opts(), {"markitdown": True, "docling": False, "mineru": False})
    assert d.engines == [] and d.reason == "no_engine_available" and d.missing == ["mineru", "docling"]
    d = route(pdf(text_ratio=1.0), opts(), {"markitdown": True, "docling": False, "mineru": True})
    assert d.engines == ["mineru", "markitdown"] and d.missing == ["docling"]


def test_engine_supports():
    assert engine_supports("mineru", ProbeResult(kind="image", ext=".png", size=1))
    assert not engine_supports("markitdown", ProbeResult(kind="image", ext=".png", size=1))
