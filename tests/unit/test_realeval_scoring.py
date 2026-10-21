"""Page-aligned scoring, paired page bootstrap and route statistics (spec 2026-10-22 §4.2, §5.2, §5.3)."""
from __future__ import annotations

import pytest

from bench.realeval import scoring as S


def _md(pages: dict[int, str]) -> str:
    return "".join(f"<!-- page: {p} -->\n{t}\n\n" for p, t in pages.items())


GT_A = "這是第一頁的正解文字共有二十個以上的字元可以計分"
GT_B = "這是第二頁的正解文字也有二十個以上的字元可以計分"


def test_pages_without_gt_are_not_scored():
    rows = S.page_rows(None, {1: GT_A, 3: GT_B}, _md({1: GT_A, 2: "x", 3: GT_B, 4: "y"}))
    assert [r["page"] for r in rows] == [1, 3] and all(r["ned"] == 1.0 for r in rows)


def test_missing_prediction_page_scores_zero():
    rows = S.page_rows(None, {1: GT_A, 2: GT_B}, _md({1: GT_A}))
    assert rows[1]["page"] == 2 and rows[1]["ned"] == 0.0 and rows[1]["dropped"] is True
    assert rows[1]["recall"] == 0.0 and rows[0]["dropped"] is False


def test_failed_conversion_scores_zero_everywhere():
    rows = S.page_rows(None, {1: GT_A, 2: GT_B}, None)
    assert all(r["ned"] == 0.0 and r["recall"] == 0.0 and r["dropped"] and r["failed"] for r in rows)
    assert S.doc_summary(rows)["ned"] == 0.0


def test_spread_page_maps_whole_section():
    right, left = "右頁的文字先出現在正解裡面", "左頁的文字後出現在正解裡面"
    rows = S.page_rows(None, {1: right + "\n" + left}, _md({1: right + " " + left}))
    assert rows[0]["ned"] == 1.0


def test_markup_and_punctuation_do_not_count():
    rows = S.page_rows(None, {1: "第一條、本法。"}, _md({1: "## 第一條\n\n**本法**"}))
    assert rows[0]["ned"] == 1.0 and rows[0]["gt_len"] == 5


def test_doc_summary_is_page_weighted_and_lists_the_worst_pages():
    rows = S.page_rows(None, {1: GT_A, 2: GT_B, 3: GT_A}, _md({1: GT_A, 3: GT_A}))
    s = S.doc_summary(rows)
    assert s["ned"] == pytest.approx(2 / 3, abs=1e-4) and s["n_pages"] == 3 and s["n_dropped"] == 1
    assert s["worst"] == {"page": 2, "ned": 0.0} and s["worst3"][0]["page"] == 2


def test_bootstrap_is_deterministic_and_covers_mean():
    diffs = [0.1] * 30 + [-0.05] * 30
    mean, lo, hi = S.paired_bootstrap(diffs)
    assert lo < mean < hi and mean == pytest.approx(0.025)
    assert S.paired_bootstrap(diffs) == (mean, lo, hi)
    assert lo > 0
    _, lo2, hi2 = S.paired_bootstrap([0.01, -0.01] * 30)
    assert not lo2 > 0 and hi2 > 0


def _rows(ned: float, n: int = 10, failed: bool = False) -> list[dict]:
    return [{"page": p, "ned": ned, "failed": failed} for p in range(1, n + 1)]


def test_route_stats_counts_wins_and_big_losses():
    per_doc = {f"d{i}": {"docling": _rows(0.83), "mineru": _rows(0.80)} for i in range(5)}
    per_doc["d5"] = {"docling": _rows(0.70), "mineru": _rows(0.76)}
    st = S.route_stats(per_doc)
    assert st["wins_D"] == 5 and st["big_loss_D"] == 1 and st["wins_M"] == 1 and st["big_loss_M"] == 0
    assert st["n_pages"] == 60 and st["n_docs_both"] == 6
    assert st["per_doc"]["d5"] == pytest.approx(-0.06)
    assert st["delta"] == pytest.approx((5 * 0.03 - 0.06) / 6)
    assert st["lo"] <= st["delta"] <= st["hi"]


def test_route_stats_failed_conversion_counts_as_zero_but_not_as_both_done():
    per_doc = {"a": {"docling": _rows(0.9), "mineru": _rows(0.0, failed=True)},
               "b": {"docling": _rows(0.9), "mineru": _rows(0.8)}}
    st = S.route_stats(per_doc)
    assert st["n_docs_both"] == 1 and st["n_pages"] == 20 and st["per_doc"]["a"] == pytest.approx(0.9)


def test_bag_prf():
    assert S.bag_prf("甲乙乙", "乙甲") == pytest.approx((2 / 3, 1.0, 0.8))
    assert S.bag_prf("甲", "") == (0.0, 0.0, 0.0)
