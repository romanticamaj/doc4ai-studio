"""Document-level formula recovery against LaTeXML alttext GT and the HTML text guard (spec 2026-10-22 §5.5)."""
from __future__ import annotations

import json

import pytest

from bench.realeval import formulas as F


@pytest.mark.parametrize(("src", "want"), [
    ("3{+}1", "3+1"),
    (r"\begin{aligned} a &= b \\ c &= d \end{aligned}", "a=bc=d"),
    (r"\label{eq:1} x \tag{3} \nonumber", "x"),
    (r"x \tag*{(A)} \notag", "x"),
    (r"\mathbf{x}\text{ if }y", "xify"),
    (r"\left( a \right)", "(a)"),
    (r"\left. f \right|_{0}", "f|_0"),
    (r"\mbox{for all } \textrm{n} \boldsymbol{\beta} \mathcal{L} \mathbb{R}", r"forall n\betaLR".replace(" ", "")),
    (r"c^{2} = a^2 {=} b", "c^2=a^2=b"),
    (r"\displaystyle \mathrm{d} x \, \quad \operatorname{tr} A", "dx{tr}A"),   # multi-char braces stay
])
def test_norm_latex_doc(src, want):
    assert F.norm_latex_doc(src) == want


def _formulas(tmp_path):
    items = [{"display": "block", "latex": "a+b"}, {"display": "inline", "latex": "x"},
             {"display": "block", "latex": "c^2"}, {"display": "inline", "latex": "y"},
             {"display": "inline", "latex": "z"}]
    p = tmp_path / "formulas.json"
    p.write_text(json.dumps(items), encoding="utf-8")
    return p


def test_display_and_inline_gt(tmp_path):
    p = _formulas(tmp_path)
    assert F.display_gt(p) == ["a+b", "c^2"]
    assert F.inline_gt(p) == ["x", "y", "z"]


def test_recovery_matches_greedily_after_normalisation():
    r = F.recovery(["a+b", "c^2"], ["c^{2}", "a + b", "zzz"])
    assert r["R"] == 1.0 and r["S"] == 1.0 and r["matched"] == 2 and r["n_gt"] == 2 and r["n_pred"] == 3


def test_recovery_without_predictions_is_zero():
    assert F.recovery(["a+b"], []) == {"R": 0.0, "S": 0.0, "n_gt": 1, "n_pred": 0, "matched": 0}


def test_a_half_match_counts_in_s_not_in_r():
    r = F.recovery(["abcd"], ["abxy"])
    assert r["R"] == 0.0 and r["S"] == pytest.approx(0.5) and r["matched"] == 0


def test_a_used_prediction_is_not_matched_twice():
    r = F.recovery(["a+b", "a+b"], ["a+b"])
    assert r["matched"] == 1 and r["R"] == 0.5


def test_html_text_drops_math_script_and_style(tmp_path):
    p = tmp_path / "paper.html"
    p.write_text('<html><head><style>p{}</style></head><body><p>Let <math alttext="x"><mi>x</mi></math> be</p>'
                 "<script>no</script></body></html>", encoding="utf-8")
    assert F.html_text(p) == "letbe"


def test_ngram_recall():
    assert F.ngram_recall("abcdefgh", "abcdefgh") == 1.0
    assert F.ngram_recall("abcdefgh", "abcde") == 0.25
    assert F.ngram_recall("abcdefgh", "") == 0.0


def test_pred_formulas_across_page_markers():
    md = ("<!-- page: 1 -->\nText $a+b$ and\n\n$$\nc^2\n$$\n\n"
          "<!-- page: 2 -->\nMore \\(x\\) then \\[y=1\\]\n")
    assert F.pred_formulas(md) == ["a+b", "\nc^2\n", "x", "y=1"]
