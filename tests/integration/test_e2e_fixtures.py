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


def test_scanned_en_routes_to_docling_with_lang_en(tmp_root, fixtures, monkeypatch):
    """§13.2 as revised in P2: Docling-first for English scans when Docling's OCR is EasyOCR (English model).
    (With the default RapidOCR, lang=en routes like cht: tests/integration/test_lang_en_real.py.)"""
    from pathlib import Path
    REPO_ROOT = Path(__file__).resolve().parents[2]
    cfg = (REPO_ROOT / "aidoc.toml").read_text(encoding="utf-8")
    assert 'docling_ocr = "rapidocr"' in cfg
    (tmp_root / "aidoc.toml").write_text(cfg.replace('docling_ocr = "rapidocr"', 'docling_ocr = "easyocr"'),
                                         encoding="utf-8")
    monkeypatch.setenv("AIDOC_CONFIG", str(tmp_root / "aidoc.toml"))
    src = tmp_root / "scanned_en.pdf"
    shutil.copy(fixtures / "scanned_en.pdf", src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--lang", "en", "--json"]) == 0
    sc = json.loads((tmp_root / "out" / "scanned_en" / "scanned_en.json").read_text(encoding="utf-8"))
    assert sc["engine"] == "docling" and sc["lang"] == "en" and sc["quality"]["level"] == "ok"


def test_big_pdf_whole_document_p1(tmp_root, fixtures):
    """Since P2 a >40-page PDF converts in 40-page segments (P1 expected one)."""
    src = tmp_root / "big.pdf"
    shutil.copy(fixtures / "big.pdf", src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--json"]) == 0
    sc = json.loads((tmp_root / "out" / "big" / "big.json").read_text(encoding="utf-8"))
    assert sc["pages"] == 45 and sc["segments"] == 2 and sc["quality"]["level"] == "ok"
