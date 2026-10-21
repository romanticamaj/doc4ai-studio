"""Formula scoring of group B against the document-level LaTeXML alttext GT, plus the body-text guard
(spec 2026-10-22 §5.5). Only display (`block`) formulas decide; inline ones are a diagnostic.

`norm_latex_doc` = `bench.metrics.norm_latex` plus exactly the pre-registered list of §5.5 — nothing more."""
from __future__ import annotations

import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from rapidfuzz.distance import Levenshtein

from aidoc.pagemap import squash
from bench.metrics import formulas_in, norm_latex
from bench.realeval.rules import FORMULA_MATCH

# §5.5 "去 \left／\right": the commands go, their delimiters stay (a null delimiter "." goes with them).
# (?![a-zA-Z]) leaves \leftarrow, \rightarrow, \leftrightarrow to norm_latex, which treats both sides alike.
_LEFT_RIGHT_CMD = re.compile(r"\\(?:left|right)(?![a-zA-Z])\s*\.?")
_DROP_ARG = re.compile(r"\\(?:label|tag\*?|begin|end)\s*\{[^{}]*\}")
_DROP_CMD = re.compile(r"\\(?:nonumber|notag)(?![a-zA-Z])")
_KEEP_CONTENT = ("mbox", "textrm", "text", "boldsymbol", "mathbf", "mathcal", "mathbb")
_KEEP_RE = re.compile(r"\\(" + "|".join(_KEEP_CONTENT) + r")(?![a-zA-Z])\s*")
_LINEBREAK = re.compile(r"\\\\")
_ALIGN = re.compile(r"(?<!\\)&")
_SINGLE_BRACE = re.compile(r"(?<!\\)\{([^{}\\])\}")


def _unwrap_commands(s: str) -> str:
    """\\text{…}, \\mbox{…}, \\textrm{…}, \\boldsymbol{…}, \\mathbf{…}, \\mathcal{…}, \\mathbb{…} → their content
    (balanced braces); the bare command (no brace argument) is dropped."""
    out: list[str] = []
    i = 0
    while True:
        m = _KEEP_RE.search(s, i)
        if not m:
            out.append(s[i:])
            return "".join(out)
        out.append(s[i:m.start()])
        j = m.end()
        if j < len(s) and s[j] == "{":
            depth, k = 0, j
            while k < len(s):
                if s[k] == "{" and (k == 0 or s[k - 1] != "\\"):
                    depth += 1
                elif s[k] == "}" and s[k - 1] != "\\":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            out.append(_unwrap_commands(s[j + 1:k]))
            i = k + 1
        else:
            i = j


def norm_latex_doc(s: str) -> str:
    s = _LEFT_RIGHT_CMD.sub("", s)
    s = _DROP_ARG.sub("", s)
    s = _DROP_CMD.sub("", s)
    s = _LINEBREAK.sub(" ", s)          # before norm_latex: its spacing rule would eat "\ " out of "\\ "
    s = _unwrap_commands(s)
    s = norm_latex(s)
    s = _ALIGN.sub("", s)
    prev = None
    while prev != s:
        prev, s = s, _SINGLE_BRACE.sub(r"\1", s)
    return s


def _items(formulas_json: Path) -> list[dict]:
    data = json.loads(Path(formulas_json).read_text(encoding="utf-8"))
    return data["formulas"] if isinstance(data, dict) else data


def display_gt(formulas_json: Path) -> list[str]:
    return [i["latex"] for i in _items(formulas_json) if i.get("display") == "block"]


def inline_gt(formulas_json: Path) -> list[str]:
    return [i["latex"] for i in _items(formulas_json) if i.get("display") == "inline"]


def pred_formulas(markdown: str) -> list[str]:
    """`$$…$$`, `$…$`, `\\[…\\]`, `\\(…\\)` of the whole output (all pages concatenated)."""
    return formulas_in(markdown)


def _sim(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return 1.0 - Levenshtein.normalized_distance(a, b)


def recovery(gt: list[str], preds: list[str], thr: float = FORMULA_MATCH) -> dict:
    """Each GT formula takes its best unused prediction (greedy, as bench.metrics.formula_ned). R = share with best
    NED ≥ thr; S = mean best NED."""
    g_n = [norm_latex_doc(g) for g in gt]
    p_n = [norm_latex_doc(p) for p in preds]
    used: set[int] = set()
    total, matched = 0.0, 0
    for g in g_n:
        best, best_i = 0.0, None
        for i, p in enumerate(p_n):
            if i in used:
                continue
            v = _sim(g, p)
            if v > best:
                best, best_i = v, i
        if best_i is not None:
            used.add(best_i)
        total += best
        matched += best >= thr
    n = len(g_n)
    return {"R": matched / n if n else 0.0, "S": total / n if n else 0.0, "n_gt": n, "n_pred": len(p_n),
            "matched": matched}


class _Visible(HTMLParser):
    SKIP = frozenset({"math", "script", "style"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)


def html_text(paper_html: Path) -> str:
    """Visible text of the arXiv HTML without <math>, <script>, <style>, squashed."""
    p = _Visible()
    p.feed(Path(paper_html).read_text(encoding="utf-8", errors="replace"))
    p.close()
    return squash(" ".join(p.parts))


def ngram_recall(gt_sq: str, pred_sq: str, n: int = 5) -> float:
    """Multiset recall of the GT's character n-grams in the prediction."""
    g = Counter(gt_sq[i:i + n] for i in range(len(gt_sq) - n + 1))
    if not g:
        return 0.0
    p = Counter(pred_sq[i:i + n] for i in range(len(pred_sq) - n + 1))
    return sum(min(c, p[k]) for k, c in g.items()) / sum(g.values())
