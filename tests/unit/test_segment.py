import pymupdf as fitz

from aidoc.segment import plan_segments, segment_dir, split_pdf


def test_plan():
    assert plan_segments(None) == [(None, None)]
    assert plan_segments(1) == [(1, 1)] and plan_segments(40) == [(1, 40)]
    assert plan_segments(41) == [(1, 40), (41, 41)]
    assert plan_segments(125) == [(1, 40), (41, 80), (81, 120), (121, 125)]


def test_split_pdf(fixtures, tmp_path):
    out = split_pdf(fixtures / "big.pdf", 41, 45, tmp_path / "seg_1.pdf")
    d = fitz.open(out)
    assert d.page_count == 5 and "第 41 頁" in d[0].get_text() and "第 45 頁" in d[4].get_text()
    d.close()


def test_segment_dir(tmp_path):
    assert segment_dir(tmp_path, 3) == tmp_path / "seg_3"


def test_extract_pages_in_order(fixtures, tmp_path):
    import pymupdf

    from aidoc.segment import extract_pages
    out = extract_pages(fixtures / "big.pdf", [7, 2, 45], tmp_path / "r.pdf")
    d = pymupdf.open(out)
    assert d.page_count == 3 and "第 7 頁" in d[0].get_text() and "第 45 頁" in d[2].get_text()
