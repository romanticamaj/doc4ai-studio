from pathlib import Path

from aidoc.models import (
    ENGINE_NAMES,
    SEGMENT_PAGES,
    TERMINAL_TASK,
    Attempt,
    ConvertOptions,
    ProbeResult,
    QualityResult,
    TableEdge,
    TaskStatus,
)


def test_convert_options_roundtrip():
    o = ConvertOptions(output_dir=Path("out"), engine="mineru", lang="en", force=True, timeout_s=30)
    d = o.to_json()
    assert d["output_dir"] == "out" and d["engine"] == "mineru"
    assert ConvertOptions.from_json(d) == o


def test_defaults():
    o = ConvertOptions(output_dir=Path("x"))
    assert o.lang == "cht" and o.mineru_tier == "basic" and o.allow_online_audio is False


def test_enums_and_constants():
    assert TaskStatus.done in TERMINAL_TASK and TaskStatus.converting not in TERMINAL_TASK
    assert SEGMENT_PAGES == 40 and ENGINE_NAMES == ("markitdown", "docling", "mineru")


def test_attempt_and_edge_roundtrip():
    a = Attempt(engine="docling", attempt=1, score=0.5, reasons=["garbage_ratio"], error_kind=None, error_msg=None)
    assert Attempt.from_json(a.to_json()) == a
    e = TableEdge(page=3, n_cols=4, touches_edge=True)
    assert TableEdge.from_json(e.to_json()) == e


def test_probe_and_quality_json():
    p = ProbeResult(kind="pdf", ext=".pdf", size=10, pages=2, text_ratio=1.0)
    assert p.to_json()["text_ratio"] == 1.0
    q = QualityResult(score=0.9, level="ok", reasons=[])
    assert q.to_json() == {"score": 0.9, "level": "ok", "reasons": [], "metrics": {}}


from aidoc.models import DocStatus, RawResult  # noqa: E402


def test_warn_level_and_doc_status():
    assert DocStatus.warn.value == "warn"
    q = QualityResult(score=0.9, level="warn", reasons=[])
    assert "page_map" not in q.to_json()                      # legacy shape when page_check is None


def test_quality_page_fields_serialise():
    q = QualityResult(score=1.0, level="ok", reasons=[], page_check=1,
                      page_map={"expected": 3, "found": 3, "coverage": 1.0},
                      pages=[{"page": 2, "reasons": ["broken_text_layer"], "repaired_by": "docling:pypdfium_full_page_ocr"},
                             {"page": 3, "reasons": ["garbage"]}], pages_flagged=2)
    j = q.to_json()
    assert j["page_check"] == 1 and j["page_map"]["coverage"] == 1.0
    assert j["pages_flagged"] == 2 and j["pages_unrepaired"] == 1


def test_probe_compacts_broken_font_pages():
    p = ProbeResult(kind="pdf", ext=".pdf", size=1, pages=3, broken_fonts=["ComicSansMS"], broken_font_pages=[1, 3])
    j = p.to_json()
    assert j["broken_fonts"] == ["ComicSansMS"] and j["broken_font_pages_count"] == 2 and "broken_font_pages" not in j


def test_raw_result_page_map_method_default():
    assert RawResult(markdown="", image_paths=[], raw_dir=Path("."), has_page_markers=False).page_map_method is None
