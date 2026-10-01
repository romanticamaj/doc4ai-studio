import pymupdf
import pytest

from aidoc.pagemap import page_map, splice_pages, split_pages, spot_check, squash

WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform", "victor", "whiskey", "xray", "yankee", "zulu"]


def _page_text(n: int) -> str:
    # unique per page: every sentence carries the page number spelled out several times
    return " ".join(f"{w}{n}x{i}" for i, w in enumerate(WORDS * 3))


@pytest.fixture
def pdf10(tmp_path):
    d = pymupdf.open()
    for n in range(1, 11):
        p = d.new_page()
        p.insert_textbox(pymupdf.Rect(50, 50, 550, 800), _page_text(n), fontsize=9)
    path = tmp_path / "p10.pdf"
    d.save(path)
    return path


def _md(shift=0, drop=()):
    parts = []
    for n in range(1, 11):
        if n in drop:
            continue
        parts.append(f"<!-- page: {n + shift} -->\n\n{_page_text(n)}\n\n")
    return "".join(parts)


def test_split_and_page_map():
    pre, secs = split_pages("intro\n<!-- page: 1 -->\nA\n<!-- page: 3 -->\nC\n<!-- page: 3 -->\nC2\n")
    assert pre == "intro\n" and set(secs) == {1, 3} and "C2" in secs[3]
    pm = page_map(_md(drop=(4, 7)), 10, "docling_per_page")
    assert pm == {"expected": 10, "found": 8, "coverage": 0.8, "missing": [4, 7], "method": "docling_per_page"}
    assert page_map("x", None, None) is None
    assert page_map("<!-- page: 99 -->", 2, "none")["found"] == 0


def test_squash_handles_hyphenation_and_cjk():
    assert squash("pres\N{SOFT HYPHEN}ence, Pres-\nence 中文 Ａ") == "presencepresence中文a"


def test_spot_check_aligned_and_shifted(pdf10):
    ok = spot_check(_md(), pdf10, sample=10)
    assert ok["decidable"] == 10 and ok["ratio"] == 1.0 and ok["misplaced"] == []
    bad = spot_check(_md(shift=1), pdf10, sample=10)
    assert bad["ratio"] <= 0.1 and len(bad["misplaced"]) >= 9


def test_spot_check_missing_text_is_undecided_not_misplaced(pdf10):
    r = spot_check(_md(drop=(5,)), pdf10, sample=10)
    assert r["decidable"] == 9 and r["ratio"] == 1.0


def test_spot_check_excludes_pages(pdf10):
    r = spot_check(_md(), pdf10, sample=10, exclude=[1, 2])
    assert r["sampled"] == 8 and r["excluded_pages"] == 2


def test_splice_pages_replaces_only_that_section():
    md = "<!-- page: 1 -->\n\nA\n\n<!-- page: 2 -->\n\nBROKEN\n\n<!-- page: 3 -->\n\nC\n"
    out = splice_pages(md, {2: "Fixed text\n\n![](assets/p2_1.png)"})
    assert out == "<!-- page: 1 -->\n\nA\n\n<!-- page: 2 -->\n\nFixed text\n\n![](assets/p2_1.png)\n\n<!-- page: 3 -->\n\nC\n"
    with pytest.raises(KeyError):
        splice_pages(md, {9: "x"})
