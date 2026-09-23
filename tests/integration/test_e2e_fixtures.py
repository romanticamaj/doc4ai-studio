import json
import shutil
import pytest
from aidoc.cli import main

pytestmark = pytest.mark.slow

CASES = [  # (fixture, expected engine, expected page markers>0)
    ("sample.docx", "markitdown", False), ("sample.pptx", "markitdown", True), ("sample.xlsx", "markitdown", False),
    ("text.pdf", "docling", True), ("scanned_cht.pdf", "mineru", True), ("scanned_mixed.pdf", "mineru", True),
    ("formula.pdf", "mineru", True), ("page.png", "mineru", False), ("twocol.pdf", "docling", True),
]


@pytest.mark.parametrize("name,engine,markers", CASES)
def test_fixture_converts_ok(tmp_root, fixtures, name, engine, markers):
    src = tmp_root / name
    shutil.copy(fixtures / name, src)
    out = tmp_root / "out"
    assert main(["convert", str(src), "-o", str(out), "--json"]) == 0
    stem = src.stem
    sc = json.loads((out / stem / f"{stem}.json").read_text(encoding="utf-8"))
    assert sc["engine"] == engine, sc["tried"]
    assert sc["quality"]["level"] == "ok", sc["quality"]
    md = (out / stem / f"{stem}.md").read_text(encoding="utf-8")
    assert ("<!-- page: " in md) == markers
    assert (out / stem / "assets").is_dir()


def test_scanned_en_routes_to_docling_with_lang_en(tmp_root, fixtures):
    src = tmp_root / "scanned_en.pdf"
    shutil.copy(fixtures / "scanned_en.pdf", src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--lang", "en", "--json"]) == 0
    sc = json.loads((tmp_root / "out" / "scanned_en" / "scanned_en.json").read_text(encoding="utf-8"))
    assert sc["engine"] == "docling" and sc["lang"] == "en"


def test_big_pdf_whole_document_p1(tmp_root, fixtures):
    """P1: >40 pages converts as ONE segment (segmentation arrives in P2)."""
    src = tmp_root / "big.pdf"
    shutil.copy(fixtures / "big.pdf", src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--json"]) == 0
    sc = json.loads((tmp_root / "out" / "big" / "big.json").read_text(encoding="utf-8"))
    assert sc["pages"] == 45 and sc["segments"] == 1 and sc["quality"]["level"] == "ok"
