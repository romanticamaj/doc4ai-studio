import math

import pytest

from bench import metrics as M


def test_ned_and_cer_basics():
    assert M.ned("abc", "abc") == 1.0 and M.ned("", "") == 1.0 and M.ned("abc", "") == 0.0
    assert math.isclose(M.ned("kitten", "sitting"), 1 - 3 / 7, rel_tol=1e-6)
    assert math.isclose(M.cer("臺灣大學", "台灣大學"), 0.25)                      # NFKC does not fold 臺/台
    assert M.ned("The quick-\nbrown", "the quick brown") == 1.0              # squash: case, hyphenation, spaces


def test_plain_strips_markup_and_math():
    assert M.ned("Title body", M.plain("## Title\n\n<!-- page: 1 -->\nbody $x^2$ ![](a.png)")) == 1.0


def test_page_scores_and_rates():
    gt = {"pages": 3, "blank_pages": [3], "page_text": {"1": "hello world " * 5, "2": "second page text " * 5, "3": ""}}
    s = M.page_scores(gt, {1: "hello world " * 5})
    assert s[1]["ned"] == 1.0 and s[2]["ned"] == 0.0 and s[2]["dropped"] and not s[3]["dropped"]
    assert M.dropped_rate(gt, s) == 0.5 and M.mean_ned(s, gt) == 0.5


def test_table_teds_perfect_and_structure_only():
    gt = [["A", "B"], ["1", "2"]]
    md = "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    assert M.table_teds(gt, md) == pytest.approx(1.0)
    assert M.table_teds(gt, "| A | B |\n| --- | --- |\n| 1 | 9 |\n", structure_only=True) == pytest.approx(1.0)
    assert M.table_teds(gt, "| A | B |\n| --- | --- |\n| 1 | 9 |\n") < 1.0
    assert M.table_teds(gt, "no table here") == 0.0
    assert M.table_teds(gt, "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>") == \
        pytest.approx(1.0)


def test_table_teds_3x3_hand_computed():
    gt = [["a", "b", "c"], ["d", "e", "f"], ["g", "h", "i"]]          # 1 table + 3 tr + 9 td = 13 nodes
    one_cell = "| a | b | c |\n| --- | --- | --- |\n| d | X | f |\n| g | h | i |\n"
    assert M.table_teds(gt, one_cell) == pytest.approx(1 - 1 / 13)    # one rename, cost 1 (single chars)
    missing_row = "| a | b | c |\n| --- | --- | --- |\n| d | e | f |\n"
    assert M.table_teds(gt, missing_row) == pytest.approx(1 - 4 / 13)  # delete tr + 3 td


def test_table_spans_need_colspan():
    gt = [["H", "M", ""], ["1", "2", "3"]]
    spans = [{"row": 0, "col": 1, "colspan": 2}]
    html = "<table><tr><td>H</td><td colspan=\"2\">M</td></tr><tr><td>1</td><td>2</td><td>3</td></tr></table>"
    assert M.table_teds(gt, html, spans=spans) == pytest.approx(1.0)
    assert M.table_teds(gt, "| H | M | |\n| --- | --- | --- |\n| 1 | 2 | 3 |\n", spans=spans) < 1.0


def test_tables_teds_matches_tables_in_order():
    sec = "text\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\nmore\n\n| C |\n| --- |\n| 3 |\n"
    got = M.tables_teds([{"cells": [["A", "B"], ["1", "2"]]}, {"cells": [["C"], ["3"]]}, {"cells": [["X"]]}], sec)
    assert got == [pytest.approx(1.0), pytest.approx(1.0), 0.0]


def test_formula_ned_normalises():
    assert M.formula_ned([r"\sum_{i=1}^{n} x_i"], r"Text $\sum_{i=1}^{n}x_i$ more") == pytest.approx(1.0)
    assert M.formula_ned([r"\frac{a}{b}"], r"$$\left(\frac{a}{b}\right)$$") == pytest.approx(1.0)
    assert M.formula_ned([r"\frac{a}{b}"], "no math") == 0.0
    assert M.formula_ned([r"\frac{a}{b}", "x^2"], r"$x^2$ and \[\frac{a}{b}\]") == pytest.approx(1.0)


def test_rates_and_rank_stats():
    scores = {1: {"ned": 0.95, "dropped": False}, 2: {"ned": 0.5, "dropped": False}, 3: {"ned": 0.0, "dropped": True}}
    assert M.recovery_rate([1, 2], scores) == 0.5 and M.recovery_rate([], scores) is None
    assert M.dropped_rate({"pages": 3, "blank_pages": []}, scores) == pytest.approx(1 / 3)
    assert M.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert M.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert M.spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.9486833, rel=1e-6)   # ties: average ranks
    assert M.precision_recall({1, 2, 3}, {2, 3, 4}) == (pytest.approx(2 / 3), pytest.approx(2 / 3))
    assert M.precision_recall(set(), set()) == (1.0, 1.0)
