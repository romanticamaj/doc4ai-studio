import json
import shutil

import pytest

from aidoc.cli import main
from tests.manual import manual_sample, require_engine

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("name,pages", [("text.pdf", 3), ("big.pdf", 45), ("paged_furniture.pdf", 8),
                                        ("span_margin.pdf", 45)])
def test_markitdown_pdf_has_a_marker_per_page(tmp_root, fixtures, name, pages):
    require_engine("markitdown")
    src = tmp_root / name
    shutil.copy(fixtures / name, src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--engine", "markitdown", "--json"]) == 0
    stem = src.stem
    md = (tmp_root / "out" / stem / f"{stem}.md").read_text(encoding="utf-8")
    sc = json.loads((tmp_root / "out" / stem / f"{stem}.json").read_text(encoding="utf-8"))
    assert md.count("<!-- page: ") == pages and sc["quality"]["page_map"]["method"] == "markitdown_per_page"


def test_markitdown_real_book_pages(tmp_root):
    require_engine("markitdown")
    src = tmp_root / "o.pdf"
    shutil.copy(manual_sample("ortho_p300-340.pdf"), src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--engine", "markitdown", "--json"]) == 0
    sc = json.loads((tmp_root / "out" / "o" / "o.json").read_text(encoding="utf-8"))
    assert sc["quality"]["page_map"]["coverage"] == 1.0
