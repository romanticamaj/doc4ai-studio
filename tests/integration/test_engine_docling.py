import re

import pytest

from aidoc.engines.docling import DoclingEngine
from aidoc.models import ConvertOptions
from aidoc.probe import probe_file

pytestmark = pytest.mark.slow


def conv(tmp_root, fixtures, name, **kw):
    e = DoclingEngine()
    assert e.available()
    pr = probe_file(fixtures / name)
    return e.convert(fixtures / name, tmp_root / "wd", ConvertOptions(output_dir=tmp_root / "out", **kw), None,
                     lambda f, line: None, timeout_s=600, probe=pr)


def test_text_pdf_pages_and_table(tmp_root, fixtures):
    r = conv(tmp_root, fixtures, "text.pdf")
    assert r.markdown.count("<!-- page: ") == 3 and "| " in r.markdown and r.page_count == 3
    assert r.first_table is not None and r.first_table.n_cols == 3


def test_scanned_cht_ocr(tmp_root, fixtures):
    r = conv(tmp_root, fixtures, "scanned_cht.pdf")
    assert "繁體中文" in r.markdown


def test_scanned_en_lang_en(tmp_root, fixtures):
    r = conv(tmp_root, fixtures, "scanned_en.pdf", lang="en")
    assert "English paragraph" in r.markdown


def test_no_duplicate_lines_per_page(tmp_root, fixtures):
    # text.pdf repeats the same paragraphs on every page by design, so duplicates are checked within a page
    # (cross-page duplication by per-page export was ruled out by spike A: per-page == whole-doc multiset)
    r = conv(tmp_root, fixtures, "text.pdf")
    for page in re.split(r"<!-- page: \d+ -->", r.markdown):
        lines = [line for line in page.splitlines() if len(line) > 30]
        assert len(lines) == len(set(lines))


def test_png_has_no_page_markers(tmp_root, fixtures):
    r = conv(tmp_root, fixtures, "page.png")
    assert "中文" in r.markdown and not r.has_page_markers and "<!-- page: " not in r.markdown


def _huge_scan(path, w=6000.0, h=4400.0):
    """A scanned page the size of the 1972 supreme-court spreads: an image of English text over a 6000×4400 pt box."""
    import pymupdf
    src = pymupdf.open()
    p = src.new_page(width=600, height=440)
    p.insert_textbox(pymupdf.Rect(30, 30, 570, 410), "This English paragraph sits on a huge scanned page. " * 6,
                     fontsize=22)
    png = p.get_pixmap(dpi=200).tobytes("png")
    doc = pymupdf.open()
    doc.new_page(width=w, height=h).insert_image(pymupdf.Rect(0, 0, w, h), stream=png)
    doc.save(path)
    return path


def test_a_huge_scanned_page_is_not_rejected_as_a_decompression_bomb(tmp_root):
    """T-003: at Docling's OCR scale 3 the page would be 237.6 M px > Pillow's 178.96 M px limit; the request gets
    a scaled copy, so the page is OCR'd instead of coming out empty."""
    pdf = _huge_scan(tmp_root / "huge.pdf")
    e = DoclingEngine()
    r = e.convert(pdf, tmp_root / "wd", ConvertOptions(output_dir=tmp_root / "out", lang="en"), None,
                  lambda f, line: None, timeout_s=900, probe=probe_file(pdf))
    assert "huge scanned page" in r.markdown.lower()
    assert not (r.failed_pages or {})
