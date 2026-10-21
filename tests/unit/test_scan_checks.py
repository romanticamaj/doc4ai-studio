"""T-002: page faults on scans without a text layer. The checks need no text layer: a page's ink (dark pixels of a
small grey render) says whether it has text and roughly how much, so an empty section on an inked page, or one
page holding its neighbour's text while the neighbour is (nearly) empty, can be flagged."""
from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from aidoc import scanchecks as S
from tests.fixtures.make_fixtures import EN, ZH, make_scanned_pdf


def _scan(tmp_path: Path, pages: list[list[str]], name: str = "scan.pdf") -> Path:
    p = tmp_path / name
    make_scanned_pdf(p, pages)
    return p


def _md(sections: dict[int, str]) -> str:
    return "".join(f"<!-- page: {p} -->\n{t}\n\n" for p, t in sections.items())


BODY = [[ZH * 2, EN], [ZH * 2, EN], [ZH * 3], [ZH * 2, EN], [ZH * 3]]      # similar volumes, as on real scans


def test_glyph_cells_are_zero_on_white_and_grow_with_text(tmp_path):
    pdf = _scan(tmp_path, [[""], [ZH], [ZH * 3]])
    ink = S.page_ink(pdf)
    assert ink[1].cells < S.CELLS_MIN
    assert S.CELLS_MIN < ink[2].cells < ink[3].cells
    assert ink[3].cells / ink[2].cells == pytest.approx(3, rel=0.35)      # cells track the amount of text


def test_ink_is_relative_to_the_paper_tone(tmp_path):
    white = _scan(tmp_path, [[ZH * 2]], "white.pdf")
    yellow = tmp_path / "yellow.pdf"
    with pymupdf.open(white) as d:
        pix = d[0].get_pixmap(dpi=100)
    out = pymupdf.open()
    page = out.new_page(width=d_w(white), height=d_h(white))
    page.draw_rect(page.rect, color=None, fill=(0.86, 0.82, 0.62))          # yellowed paper
    page.insert_image(page.rect, stream=pix.tobytes("png"), overlay=True, alpha=0)
    out.save(yellow)
    a, b = S.page_ink(white)[1].cells, S.page_ink(yellow)[1].cells
    assert abs(a - b) / a < 0.3


def d_w(pdf):
    with pymupdf.open(pdf) as d:
        return d[0].rect.width


def d_h(pdf):
    with pymupdf.open(pdf) as d:
        return d[0].rect.height


def _texts(pages):
    return {i: "\n".join(t) for i, t in enumerate(pages, 1)}


def test_a_faithful_conversion_raises_nothing(tmp_path):
    pdf = _scan(tmp_path, BODY)
    assert S.scan_page_checks(_md(_texts(BODY)), S.page_ink(pdf)) == []


def test_an_empty_section_on_an_inked_page(tmp_path):
    pdf = _scan(tmp_path, BODY)
    secs = _texts(BODY)
    secs[3] = ""
    out = S.scan_page_checks(_md(secs), S.page_ink(pdf))
    assert [(e["page"], e["reasons"]) for e in out] == [(3, ["page_empty_scan"])]
    assert out[0]["metrics"]["scan"] is True and out[0]["metrics"]["cells"] > S.CELLS_MIN


def test_a_short_page_with_little_ink_is_not_empty(tmp_path):
    pages = BODY + [["盧國維記"]]
    pdf = _scan(tmp_path, pages)
    secs = _texts(pages)
    secs[6] = "盧國維記"
    assert S.scan_page_checks(_md(secs), S.page_ink(pdf)) == []


def test_a_missing_marker_counts_as_an_empty_section(tmp_path):
    pdf = _scan(tmp_path, BODY)
    secs = _texts(BODY)
    del secs[2]
    assert [e["page"] for e in S.scan_page_checks(_md(secs), S.page_ink(pdf))] == [2]


def test_text_moved_to_the_previous_page(tmp_path):
    """MinerU on the 1973 court letter / AASHTO p5: page 4's section holds page 5's text, page 5 is empty."""
    pdf = _scan(tmp_path, BODY)
    secs = _texts(BODY)
    secs[4] = secs[4] + "\n" + secs[5]
    secs[5] = ""
    out = {e["page"]: e for e in S.scan_page_checks(_md(secs), S.page_ink(pdf))}
    assert out[4]["reasons"] == ["page_oversized"] and out[5]["reasons"] == ["page_misaligned"]
    assert out[4]["metrics"]["scan"] and out[4]["metrics"]["pair"] == [5] and out[5]["metrics"]["pair"] == [4]


def test_text_of_two_pages_moved_into_one(tmp_path):
    """MinerU on the 1973 court letter: page 1 holds pages 2 and 3."""
    pages = BODY + [[ZH * 2, EN]]
    pdf = _scan(tmp_path, pages)
    secs = _texts(pages)
    secs[1] = secs[1] + secs[2] + secs[3]
    secs[2] = secs[3] = ""
    out = {e["page"]: e["reasons"] for e in S.scan_page_checks(_md(secs), S.page_ink(pdf))}
    assert out == {1: ["page_oversized"], 2: ["page_misaligned"], 3: ["page_misaligned"]}


def test_a_page_cut_short_with_its_text_on_the_previous_page(tmp_path):
    """Docling on the 1702 pamphlet: page 3 cut to a few characters, its text in page 2."""
    pdf = _scan(tmp_path, BODY)
    secs = _texts(BODY)
    cut = len(secs[3]) // 8
    secs[2] = secs[2] + "\n" + secs[3][cut:]
    secs[3] = secs[3][:cut]
    out = {e["page"]: e["reasons"] for e in S.scan_page_checks(_md(secs), S.page_ink(pdf))}
    assert out == {2: ["page_oversized"], 3: ["page_misaligned"]}


def test_a_dense_page_next_to_a_title_page_is_not_misattribution(tmp_path):
    """Uneven density alone is normal (a title page in big type next to a dense page): the excess of one page must
    match the deficit of its neighbours."""
    pdf = tmp_path / "title.pdf"
    doc = pymupdf.open()
    for kind in ("dense", "title", "body", "body", "body"):
        page = doc.new_page()
        if kind == "title":
            page.insert_textbox(pymupdf.Rect(60, 200, 540, 600), "人間詞話", fontsize=96, fontname="china-t")
        else:
            page.insert_textbox(pymupdf.Rect(50, 50, 560, 800), ZH * (8 if kind == "dense" else 4),
                                fontsize=9 if kind == "dense" else 14, fontname="china-t")
    raster = pymupdf.open()
    for page in doc:
        png = page.get_pixmap(dpi=150).tobytes("png")
        raster.new_page(width=page.rect.width, height=page.rect.height).insert_image(page.rect, stream=png)
    raster.save(pdf)
    secs = {1: ZH * 8, 2: "人間詞話", 3: ZH * 4, 4: ZH * 4, 5: ZH * 4}
    assert S.scan_page_checks(_md(secs), S.page_ink(pdf)) == []


def test_pages_with_a_text_layer_are_not_judged_here(tmp_path):
    fix = Path(__file__).resolve().parents[1] / "fixtures"
    ink = S.page_ink(fix / "text.pdf")
    assert ink and all(v is None for v in ink.values())   # text-layer pages: the layer-based checks own them
    assert S.scan_page_checks(_md({1: "", 2: "", 3: ""}), ink) == []


@pytest.mark.parametrize("n", [1, 2])
def test_too_few_pages_for_a_density_judgement_still_get_the_empty_check(tmp_path, n):
    pages = BODY[:n]
    pdf = _scan(tmp_path, pages)
    secs = _texts(pages)
    secs[n] = ""
    assert [e["reasons"] for e in S.scan_page_checks(_md(secs), S.page_ink(pdf))] == [["page_empty_scan"]]


def test_blank_pages_are_skipped(tmp_path):
    pdf = _scan(tmp_path, BODY)
    secs = _texts(BODY)
    secs[3] = ""
    assert S.scan_page_checks(_md(secs), S.page_ink(pdf), blank_pages=[3]) == []


def test_a_photo_page_is_not_judged_as_text(tmp_path):
    """A cover photo (the labelled ortho2 p1: 56 % ink) is a picture: an engine that outputs only its image link is
    right, not a page_empty_scan (synthetic text pages hold 0.4–19 % ink)."""
    pdf = tmp_path / "photo.pdf"
    doc = pymupdf.open()
    for i in range(4):
        page = doc.new_page()
        if i == 0:                                   # a photo on paper: dark tones over ~70 % of the page
            for k in range(60, 760, 6):
                page.draw_rect(pymupdf.Rect(40, k, 555, k + 6), color=None,
                               fill=(0.05 + (k % 60) / 300, 0.1, 0.15))
        else:
            page.insert_textbox(pymupdf.Rect(50, 50, 560, 800), ZH * 6, fontsize=14, fontname="china-t")
    raster = pymupdf.open()
    for page in doc:
        png = page.get_pixmap(dpi=100).tobytes("png")
        raster.new_page(width=page.rect.width, height=page.rect.height).insert_image(page.rect, stream=png)
    raster.save(pdf)
    ink = S.page_ink(pdf)
    assert ink[1].ink > S.INK_TEXT_MAX
    secs = {1: "![](images/cover.png)", 2: ZH * 6, 3: ZH * 6, 4: ZH * 6}
    assert S.scan_page_checks(_md(secs), ink) == []


def test_thresholds_are_frozen():
    """Set on the design set (bench/scan_calib.py: synthetic Tier A scans + the design label pages; T-002) and
    frozen before the real-sample regression set was looked at. A change here is a re-calibration, on new data."""
    assert (S.INK_DPI, S.INK_MARGIN, S.INK_PAPER_PCT, S.INK_DARK) == (72, 0.03, 0.90, 0.55)
    assert (S.BAND_MIN_SHARE, S.BAND_MIN_PX, S.BAND_MAX_SHARE) == (0.004, 3, 0.15)
    assert (S.PITCH_MIN_PX, S.PITCH_MAX_SHARE, S.PITCH_MIN_STRENGTH, S.LINES_MIN, S.STRIPS, S.PITCH_PEAK_REL) == \
        (4, 0.15, 0.25, 3, 6, 0.5)
    assert (S.LINE_GAP, S.GLYPH_FILL, S.SCAN_LAYER_MAX, S.EMPTY_SECTION_MAX) == (1.6, 0.12, 20, 20)
    assert (S.CELLS_MIN, S.INK_TEXT_MAX, S.EMPTY_CELLS_REL, S.DENSITY_MIN_PAGES) == (20, 0.35, 0.10, 3)
    assert (S.OVER, S.UNDER, S.EXCESS_MIN, S.BALANCE) == (1.5, 0.5, 40, (0.5, 2.0))


def test_a_text_pdf_is_not_rendered(tmp_path, monkeypatch):
    """Known text layers on every page: page_ink answers without opening the PDF (no render cost on text PDFs)."""
    assert S.page_ink(tmp_path / "does-not-exist.pdf", layers=["x" * 40, "y" * 40]) == {1: None, 2: None}
