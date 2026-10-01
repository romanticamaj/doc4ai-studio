"""The real-document gate (spec 2026-10-01 §10.3). Run: AIDOC_REQUIRE_MANUAL=1 uv run pytest -m slow tests/integration"""
import json
import re
import shutil

import pytest

from aidoc.cli import main
from tests.manual import manual_sample, require_engine

pytestmark = pytest.mark.slow
MARK = re.compile(r"<!-- page: (\d+) -->")


def _convert(tmp_root, sample, *args):
    src = tmp_root / sample.name
    shutil.copy(sample, src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--json", *args]) == 0
    d = tmp_root / "out" / src.stem
    return (d / f"{src.stem}.md").read_text(encoding="utf-8"), json.loads((d / f"{src.stem}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name,pages", [("ortho_p1-80.pdf", 80), ("ortho_p300-340.pdf", 41), ("ortho_blanks.pdf", 6)])
def test_mineru_marks_every_page_of_real_book(tmp_root, name, pages):
    require_engine("mineru")
    md, sc = _convert(tmp_root, manual_sample(name), "--engine", "mineru")
    assert [int(n) for n in MARK.findall(md)] == list(range(1, pages + 1))
    pm = sc["quality"]["page_map"]
    assert pm["coverage"] == 1.0 and pm["method"] == "mineru_render_plan" and pm["alignment"]["ratio"] >= 0.9
    assert sc["quality"]["level"] in ("ok", "warn")


def test_mineru_marks_every_page_of_furniture_fixture(tmp_root, fixtures):
    require_engine("mineru")
    md, sc = _convert(tmp_root, fixtures / "paged_furniture.pdf", "--engine", "mineru")
    assert MARK.findall(md) == [str(n) for n in range(1, 9)] and sc["quality"]["page_map"]["coverage"] == 1.0


def _norm(s):
    return " ".join(s.lower().replace("\N{RIGHT SINGLE QUOTATION MARK}", "'").split())


def test_t001_cover_detected_and_repaired(tmp_root):
    require_engine("docling")
    md, sc = _convert(tmp_root, manual_sample("hfad_cover_p1.pdf"), "--engine", "docling")
    assert "wouldn't it be dreamy" in _norm(md)
    assert sc["quality"]["pages"][0]["repaired_by"].startswith("docling:") and sc["quality"]["pages_unrepaired"] == 0


def test_t001_mix_detection_and_repair(tmp_root):
    require_engine("docling")
    md, sc = _convert(tmp_root, manual_sample("hfad_garbled_mix.pdf"), "--engine", "docling")
    q = sc["quality"]
    assert sorted(p["page"] for p in q["pages"]) == [1, 2, 3, 4, 5, 6, 7]        # pages 8, 9 are the controls
    assert q["pages_unrepaired"] == 0 and q["level"] == "ok"
    secs = dict(zip(map(int, MARK.findall(md)), MARK.split(md)[2::2]))
    assert "with all these different devices" in _norm(secs[4])
    assert "there's some major" in _norm(secs[5]) and "java code" in _norm(secs[6])


def test_synthetic_broken_tounicode_end_to_end(tmp_root, fixtures):
    require_engine("docling")
    require_engine("mineru")
    md, sc = _convert(tmp_root, fixtures / "broken_tounicode.pdf")              # auto routing: docling first
    # 1 of 2 pages flagged (50 % > 20 %): docling attempt is low (pages_flagged) -> whole document on mineru
    assert sc["tried"][0]["engine"] == "docling" and "pages_flagged" in sc["tried"][0]["reasons"]
    assert sc["engine"] == "mineru" and "wouldn't it be dreamy" in _norm(md)
