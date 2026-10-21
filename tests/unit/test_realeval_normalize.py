"""Normalisation policy of the real-sample evaluation (spec 2026-10-22 §5.1, §4.4, §5.4)."""
from __future__ import annotations

import json

import pytest
from rapidfuzz.distance import Levenshtein

from bench import metrics as M
from bench.realeval import normalize as N


def test_whitespace_and_line_breaks_vanish():
    assert N.norm_gt("甲\n乙 丙") == "甲乙丙"


def test_long_s_and_ligature():
    assert N.norm_gt("becauſe ﬁrſt") == "becausefirst"
    assert N.norm_pred("because first") == "becausefirst"


def test_full_width_digits():
    assert N.norm_gt("１９７３年") == "1973年"


def test_punctuation_and_gt_table_pipes_dropped():
    assert N.norm_gt("一、任意搶擄者槍斃 | 甲") == "一任意搶擄者槍斃甲"


def test_bopomofo_kept_tone_marks_dropped():
    assert N.norm_gt("ㄌㄥˇ（冷）") == "ㄌㄥ冷"
    assert N.norm_pred("ㄇㄚˊ ㄇㄚˋ ㄇㄚ˙") == "ㄇㄚㄇㄚㄇㄚ"


def test_simplified_vs_traditional_is_an_error():
    a, b = N.norm_gt("臺灣"), N.norm_pred("台灣")
    assert M.ned(a, b) < 1.0 and Levenshtein.distance(a, b) == 1


def test_variants_are_errors_and_counted_by_the_diagnostic():
    assert Levenshtein.distance(N.norm_gt("爲"), N.norm_pred("為")) == 1
    assert N.variant_share("爲", "為") == 1.0
    assert N.variant_share("甲乙", "甲丙") == 0.0
    assert N.variant_share("甲乙", "甲乙") is None


def test_prediction_markup_removed_math_unwrapped():
    """Spec amendment 2026-10-22 (§5.1 step 1): math delimiters go, their content stays."""
    md = "# 標題\n\n<!-- page: 3 -->\n| a | b |\n|---|---|\n![](x.png) $x^2$ text"
    assert N.norm_pred(md) == "標題abx2text"


def test_math_wrapped_digits_and_cjk_score_like_plain_text():
    """MinerU wraps digits / CJK / fractions in $…$, Docling does not: both must score the same (amendment §5.1)."""
    from bench.realeval import scoring
    gt = "中華民國六十二年 1973 年 第 12 號 臺北 地方法院 二分之一 12 甲乙"
    mineru = r"中華民國六十二年 $1973$ 年 第 $\mathrm{12}$ 號 $\text{臺北}$ 地方法院 二分之一 $\frac{1}{2}$ 甲乙"
    docling = "中華民國六十二年 1973 年 第 12 號 臺北 地方法院 二分之一 12 甲乙"
    assert N.norm_pred(mineru) == N.norm_pred(docling)
    rm = scoring.page_rows(None, {1: gt}, f"<!-- page: 1 -->\n{mineru}")[0]
    rd = scoring.page_rows(None, {1: gt}, f"<!-- page: 1 -->\n{docling}")[0]
    assert rm["ned"] == rd["ned"] == 1.0 and rm["recall"] == rd["recall"]


@pytest.mark.parametrize(("md", "want"), [
    (r"\(x+1\)", "x1"), (r"\[ \alpha = 3 \]", "3"), ("$$\n\\sum_{i=1}^{n} a_i\n$$", "i1nai"),
    (r"$\{a\}$", "a"), ("價格 $5$ 元", "價格5元"),
])
def test_unwrap_math_forms(md, want):
    assert N.norm_pred(md) == want


def test_apostrophes_unified():
    assert N.norm_pred("Wouldn’t") == N.norm_gt("Wouldn't") == N.norm_gt("Wouldnʼt")


def test_identical_function_on_both_sides():
    s = "第三條　本法自公布日施行。Section 3 applies."
    assert N.norm_gt(s) == N.norm_pred(s)


def _errata(wrong="ㄌㄙ（冷）", right="ㄌㄥ（冷）"):
    return [{"doc": "zh-ssid-zhuyin-primer", "page": 12, "wrong": wrong, "right": right, "source": "SOURCE.md"},
            {"doc": "other-doc", "page": 12, "wrong": "x", "right": "y", "source": "-"}]


def test_errata_replace_exactly_once():
    pages = {11: "甲", 12: "前 ㄌㄙ（冷） 後"}
    got = N.apply_errata(pages, _errata(), "zh-ssid-zhuyin-primer")
    assert got == {11: "甲", 12: "前 ㄌㄥ（冷） 後"} and pages[12] == "前 ㄌㄙ（冷） 後"   # the input is not mutated


@pytest.mark.parametrize("text", ["ㄌㄙ（冷）ㄌㄙ（冷）", "沒有"])
def test_errata_that_do_not_match_exactly_once_raise(text):
    with pytest.raises(ValueError):
        N.apply_errata({12: text}, _errata(), "zh-ssid-zhuyin-primer")


def test_errata_for_a_page_without_gt_raise():
    with pytest.raises(ValueError):
        N.apply_errata({11: "甲"}, _errata(), "zh-ssid-zhuyin-primer")


def test_variants_file_is_characters_only():
    pairs = json.loads(N.VARIANTS.read_text(encoding="utf-8"))
    assert len(pairs) >= 25
    assert all(len(a) == 1 and len(b) == 1 and a != b for a, b in pairs)
