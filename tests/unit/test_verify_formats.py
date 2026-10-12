"""verify on real third-party native outputs of paged_furniture.pdf (spec 2026-10-05 verify §6)."""
from pathlib import Path

from aidoc.verify import verify_markdown
from tests.manual import recorded_fixture

FIX = Path(__file__).resolve().parents[1] / "fixtures"
PDF = FIX / "paged_furniture.pdf"


def test_mineru_native_markdown_is_inferred():
    md = (FIX / "mineru_recorded" / "furniture8" / "markdown.md").read_text(encoding="utf-8")   # real MinerU output
    r = verify_markdown(md, PDF, fmt="auto")
    assert r.input["format_detected"] in ("none", "mineru") and r.page_map["method"] == "inferred"
    assert r.page_map["anchored"] >= 6 and r.level != "low" and r.verifiable is True


def test_docling_native_placeholder_output():
    md = recorded_fixture("docling_recorded/furniture8/export.md").read_text(encoding="utf-8")
    r = verify_markdown(md, PDF, fmt="docling")
    # Docling drops the blank page 4 -> 7 sections for 8 pages -> markers not applied -> inferred
    assert r.input["markers_found"] == 6 and r.input["markers_reason"] == "section_count_mismatch"
    assert r.page_map["method"] == "inferred" and r.page_map["anchored"] >= 6 and r.level != "low"
    auto = verify_markdown(md, PDF)
    assert auto.input["format_detected"] == "docling" and auto.page_map["method"] == "inferred"


def test_markitdown_native_output_inferred():
    md = recorded_fixture("markitdown_recorded/furniture8/convert.md").read_text(encoding="utf-8")
    r = verify_markdown(md, PDF)
    assert r.page_map["method"] == "inferred" and r.page_map["anchored"] >= 5 and r.level != "low"
