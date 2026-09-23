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
