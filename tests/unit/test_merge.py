from aidoc.models import TableEdge
from aidoc.segment import SegmentPart, merge_segments, split_leading_gfm_table, split_trailing_gfm_table

T1 = "<!-- page: 40 -->\ntext\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n"
T2 = "<!-- page: 41 -->\n| a | b |\n| --- | --- |\n| 3 | 4 |\n\nafter\n"


def part(idx, ps, pe, md, first=None, last=None, markers=True, pc=None):
    return SegmentPart(idx, ps, pe, md, [], markers, first, last, pc or (pe - ps + 1))


def test_merge_concatenates_and_moves_assets(tmp_path):
    a = part(0, 1, 40, "<!-- page: 1 -->\nA\n"); a.assets = [(tmp_path / "x.png", "p1_1.png")]
    b = part(1, 41, 45, "<!-- page: 41 -->\nB\n"); b.assets = [(tmp_path / "y.png", "p41_1.png")]
    r = merge_segments([a, b])
    assert r.markdown == "<!-- page: 1 -->\nA\n\n<!-- page: 41 -->\nB\n"
    assert [n for _, n in r.assets] == ["p1_1.png", "p41_1.png"]


def test_segment_without_markers_gets_start_marker():
    r = merge_segments([part(0, 1, 40, "A\n", markers=False), part(1, 41, 45, "B\n", markers=False)])
    assert r.markdown.startswith("<!-- page: 1 -->\nA") and "<!-- page: 41 -->\nB" in r.markdown


def test_edge_tables_merged_same_header():
    p = part(0, 1, 40, T1, last=TableEdge(page=40, n_cols=2, touches_edge=True))
    n = part(1, 41, 45, T2, first=TableEdge(page=1, n_cols=2, touches_edge=True))
    r = merge_segments([p, n]).markdown
    assert r.count("| a | b |") == 1 and "| 1 | 2 |\n| 3 | 4 |" in r and r.index("| 3 | 4 |") < r.index("<!-- page: 41 -->")
    assert "after" in r


def test_edge_tables_not_merged_when_cols_differ_or_no_bbox():
    p = part(0, 1, 40, T1, last=TableEdge(page=40, n_cols=3, touches_edge=True))
    n = part(1, 41, 45, T2, first=TableEdge(page=1, n_cols=2, touches_edge=True))
    assert merge_segments([p, n]).markdown.count("| a | b |") == 2
    p2 = part(0, 1, 40, T1); n2 = part(1, 41, 45, T2)          # no edge info at all
    assert merge_segments([p2, n2]).markdown.count("| a | b |") == 2
    p3 = part(0, 1, 40, T1, last=TableEdge(page=39, n_cols=2, touches_edge=True))   # table not on last page
    assert merge_segments([p3, n]).markdown.count("| a | b |") == 2


def test_edge_tables_not_merged_when_not_touching_edge_or_md_disagrees():
    n = part(1, 41, 45, T2, first=TableEdge(page=1, n_cols=2, touches_edge=True))
    p = part(0, 1, 40, T1, last=TableEdge(page=40, n_cols=2, touches_edge=False))
    assert merge_segments([p, n]).markdown.count("| a | b |") == 2
    # edge info claims 2 columns but the markdown tail is not a table
    p4 = part(0, 1, 40, T1 + "\ntrailing text\n", last=TableEdge(page=40, n_cols=2, touches_edge=True))
    assert merge_segments([p4, n]).markdown.count("| a | b |") == 2


def test_edge_tables_different_header_keeps_row():
    n = part(1, 41, 45, "<!-- page: 41 -->\n| c | d |\n| --- | --- |\n| 3 | 4 |\n",
             first=TableEdge(page=1, n_cols=2, touches_edge=True))
    p = part(0, 1, 40, T1, last=TableEdge(page=40, n_cols=2, touches_edge=True))
    r = merge_segments([p, n]).markdown
    assert "| 1 | 2 |\n| c | d |\n| 3 | 4 |" in r and r.count("| --- | --- |") == 1


def test_split_helpers():
    before, rows = split_trailing_gfm_table(T1)
    assert rows == ["| a | b |", "| --- | --- |", "| 1 | 2 |"] and before.rstrip().endswith("text")
    rows2, rest = split_leading_gfm_table(T2)
    assert rows2[-1] == "| 3 | 4 |" and rest.startswith("<!-- page: 41 -->") and "after" in rest
    assert split_trailing_gfm_table("no table\n") == ("no table\n", [])
    assert split_leading_gfm_table("x\n| a |\n") == ([], "x\n| a |\n")
