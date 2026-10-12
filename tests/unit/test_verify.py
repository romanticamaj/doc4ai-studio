import json
from pathlib import Path

import pymupdf
import pytest

from aidoc.pagemap import MARKER, split_pages
from aidoc.verify import verify_markdown

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _own_output(pdf):
    with pymupdf.open(pdf) as d:
        return "".join(f"<!-- page: {i} -->\n\n{p.get_text().strip()}\n\n" for i, p in enumerate(d, 1))


def test_clean_document_is_ok_and_json_matches_sidecar_keys():
    r = verify_markdown(_own_output(FIX / "paged_furniture.pdf"), FIX / "paged_furniture.pdf")
    j = r.to_json()
    assert r.level == "ok" and r.verifiable is True and j["page_map"]["coverage"] == 1.0
    assert j["input"]["format_detected"] == "aidoc" and "format_forced" not in j["input"]
    assert {"level", "score", "reasons", "pages_flagged", "pages_unrepaired", "advisories"} <= set(j["summary"])
    assert j["pdf"]["pages"] == 8 and j["pdf"]["blank_pages"] == [4] and j["pdf"]["text_layer_pages"] == 7
    assert j["page_map"]["alignment"]["decidable"] >= 5
    assert {"probe", "layers", "assess", "total"} <= set(j["timing_s"])
    from aidoc.quality import PAGE_CHECK_VERSION
    from aidoc.reassess import doc_flags  # Library can consume summary + page_map unchanged
    assert doc_flags({"quality": {**j["summary"], "page_map": j["page_map"], "page_check": PAGE_CHECK_VERSION},
                      "source_path": "x.pdf"}) == []
    json.dumps(j)


def test_dropped_page_is_page_level_warn_not_low():
    md = _own_output(FIX / "paged_furniture.pdf")
    _, secs = split_pages(md)
    md = md.replace(secs[3], "\n\n")                                  # page 3 silently emptied, marker kept
    r = verify_markdown(md, FIX / "paged_furniture.pdf")
    assert r.level == "warn" and r.summary["reasons"] == []
    assert [p["page"] for p in r.pages if "page_dropped" in p["reasons"]] == [3]
    assert r.summary["pages_unrepaired"] == 1 and r.summary["pages_flagged"] == 1
    e = next(p for p in r.pages if p["page"] == 3)
    assert e["metrics"]["section_chars"] == 0 and e["metrics"]["layer_chars"] >= 120


def test_many_dropped_pages_make_the_document_low_like_any_flagged_page():
    """Like every flagged page (spec 2026-10-01 §5.2), dropped pages over 20 % of the content pages mean the output is
    unusable: 2 of 7 here."""
    md = _own_output(FIX / "paged_furniture.pdf")
    _, secs = split_pages(md)
    md = md.replace(secs[3], "\n\n").replace(secs[6], "\n\n")
    r = verify_markdown(md, FIX / "paged_furniture.pdf")
    assert r.level == "low" and r.summary["reasons"] == ["pages_flagged"]
    assert [p["page"] for p in r.pages if "page_dropped" in p["reasons"]] == [3, 6]


def test_blank_page_is_never_dropped():
    r = verify_markdown(_own_output(FIX / "paged_furniture.pdf"), FIX / "paged_furniture.pdf")
    assert all(p["page"] != 4 for p in r.pages)


def test_shifted_markers_are_low():
    md = MARKER.sub(lambda m: f"<!-- page: {int(m.group(1)) + 1} -->", _own_output(FIX / "paged_furniture.pdf"))
    r = verify_markdown(md, FIX / "paged_furniture.pdf")
    assert r.level == "low"
    assert "page_map_invalid" in r.summary["reasons"] or "page_map_misaligned" in r.summary["reasons"]


def test_no_markers_is_inferred_and_coverage_is_null():
    md = MARKER.sub("", _own_output(FIX / "paged_furniture.pdf"))
    r = verify_markdown(md, FIX / "paged_furniture.pdf")
    assert r.page_map["method"] == "inferred" and r.page_map["coverage"] is None and r.page_map["found"] is None
    assert r.page_map["anchored"] >= 6 and r.page_map["decidable"] >= r.page_map["anchored"]
    assert "page_map_incomplete" not in r.summary["reasons"] and r.verifiable is True and r.level == "ok"
    assert r.input["format_detected"] == "none"


def test_no_markers_with_a_lost_page_flags_it():
    _, secs = split_pages(_own_output(FIX / "paged_furniture.pdf"))
    md = "\n\n".join(secs[p] for p in (1, 2, 3, 4, 6, 7, 8))
    r = verify_markdown(md, FIX / "paged_furniture.pdf")
    assert r.level == "warn" and [p["page"] for p in r.pages if "page_dropped" in p.get("reasons", [])] == [5]


def test_scanned_without_markers_is_unverifiable():
    r = verify_markdown("這是一些 OCR 出來的文字，沒有頁碼。", FIX / "scanned_cht.pdf")
    assert r.verifiable is False and r.page_map is None and r.level in ("ok", "warn", "low")
    assert r.pdf["text_layer_pages"] == 0


def test_no_pdf_partial_with_pages():
    md = _own_output(FIX / "text.pdf")
    r = verify_markdown(md, None, pages=3)
    assert r.verifiable == "partial" and r.page_map["coverage"] == 1.0 and r.page_map.get("alignment") is None
    assert r.pdf is None
    with pytest.raises(ValueError):
        verify_markdown("x", None)                                     # neither pdf nor pages


def test_no_pdf_and_no_markers_has_no_page_map():
    r = verify_markdown("plain text " * 50, None, pages=3)
    assert r.verifiable == "partial" and r.page_map is None and "page_map_incomplete" not in r.summary["reasons"]


def test_broken_text_layer_page_is_flagged_without_repair_info():
    # the committed broken_tounicode.pdf: page 1 glyph-id text. PyMuPDF's raw layer of that line is only 4 % garbage
    # (below PAGE_GARBAGE_MAX), so the "third-party output" carries what Docling's pdfium backend really emits for
    # glyph-id text: U+FFFD between characters
    md = _own_output(FIX / "broken_tounicode.pdf").replace(
        "<!-- page: 2 -->", "W�o�u�l�d�n\n\n<!-- page: 2 -->")
    r = verify_markdown(md, FIX / "broken_tounicode.pdf")
    flagged = {p["page"]: p["reasons"] for p in r.pages if p.get("reasons")}
    assert 1 in flagged and "broken_text_layer" in flagged[1] and 2 not in flagged
    assert r.summary["pages_unrepaired"] >= 1


def test_table_advisory_on_text_pdf_page_2_when_table_missing():
    md = _own_output(FIX / "text.pdf")                                # plain text layer: table cells but no GFM table
    r = verify_markdown(md, FIX / "text.pdf")
    adv = {p["page"]: p.get("advisories", []) for p in r.pages}
    assert "table_missing_on_page" in adv.get(2, []) and r.summary["advisories"] == 1
    # the advisory never changes the level: the level is the document rule (missing_table -> low, spec §2.3)
    assert r.summary["reasons"] == ["missing_table"] and r.level == "low"
    assert not any(p.get("reasons") for p in r.pages) and r.summary["pages_flagged"] == 0


def test_formula_advisory_and_images_missing():
    md = _own_output(FIX / "formula.pdf").replace("<!-- page: 2 -->\n\n", "<!-- page: 2 -->\n\n<!-- image missing -->\n")
    r = verify_markdown(md, FIX / "formula.pdf")
    adv = {p["page"]: p.get("advisories", []) for p in r.pages}
    assert "formula_missing_on_page" in adv[1] and "images_missing" in adv[2] and r.metrics["images_missing"] == 1
    md2 = md.replace("<!-- page: 1 -->\n\n", "<!-- page: 1 -->\n\n$x^2$\n")
    adv2 = {p["page"]: p.get("advisories", []) for p in verify_markdown(md2, FIX / "formula.pdf").pages}
    assert "formula_missing_on_page" not in adv2.get(1, [])


def test_crlf_input_and_custom_regex():
    md = _own_output(FIX / "text.pdf").replace("<!-- page: ", "[[p ").replace(" -->", "]]").replace("\n", "\r\n")
    r = verify_markdown(md, FIX / "text.pdf", fmt="custom", marker_regex=r"\[\[p (\d+)\]\]")
    assert r.input["newline"] == "crlf" and r.page_map["coverage"] == 1.0 and r.input["format_forced"] is True


def test_unreadable_pdf_raises():
    with pytest.raises(ValueError, match="pdf_unreadable"):
        verify_markdown("x", FIX / "corrupt.pdf")


def test_pure_no_files_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    verify_markdown(_own_output(FIX / "text.pdf"), FIX / "text.pdf")
    assert list(tmp_path.iterdir()) == []


def test_unverifiable_says_why_when_the_pdf_has_text():
    """A digital PDF whose Markdown shares no text with it (e.g. an empty or wrong file) is unverifiable for another
    reason than a scan: the summary must not call it a scanned PDF."""
    from aidoc.verify import summary_text
    r = verify_markdown("completely unrelated words " * 20, FIX / "paged_furniture.pdf")
    assert r.verifiable is False
    en = summary_text(r, "en")
    assert "scanned" not in en and "matches" in en
    scan = summary_text(verify_markdown("沒有頁碼", FIX / "scanned_cht.pdf"), "en")
    assert "scanned" in scan


def test_verify_checks_every_page_and_reports_page_level_reasons():
    """Verifier round: swapped content under valid markers, a cut page — verify checks every page by default
    (alignment over all decidable pages) and reports page_misaligned / page_partial."""
    _, secs = split_pages(_own_output(FIX / "paged_furniture.pdf"))
    secs = dict(secs)
    secs[3], secs[6] = secs[6], secs[3]
    secs[5] = secs[5][: int(len(secs[5]) * 0.15)]
    md = "".join(f"<!-- page: {p} -->\n\n{secs[p]}\n\n" for p in sorted(secs))
    r = verify_markdown(md, FIX / "paged_furniture.pdf")
    reasons = {p["page"]: p.get("reasons") for p in r.pages if p.get("reasons")}
    assert reasons[3] == ["page_misaligned"] and reasons[6] == ["page_misaligned"] and reasons[5] == ["page_partial"]
    assert r.page_map["alignment"]["sampled"] == 7                     # every page with text, not a sample of 30


def test_every_page_is_the_default_in_the_function_and_the_cli():
    import inspect

    from aidoc.cli import build_parser
    assert inspect.signature(verify_markdown).parameters["sample"].default == 0
    assert build_parser().parse_args(["verify", "x.md"]).sample == 0
