"""Benchmark metrics (spec 2026-10-05 verify §3.4). Everything is compared after normalisation — never Markdown
syntax: `quality.strip_markup()` removes comments, image links, table rules and markup characters, `$…$` math is
measured by `formula_ned` only, and `pagemap.squash()` (NFKC, lower-case, alphanumerics only) runs last.

TEDS is an in-repo implementation (Zhang–Shasha tree edit distance over the table's HTML tree, PubTabNet's
definition), so no third-party TEDS package (and its licence) is involved.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

from rapidfuzz.distance import Levenshtein

from aidoc.pagemap import squash
from aidoc.quality import strip_markup

DROPPED_SECTION_MAX = 20
RECOVERED_NED = 0.90
_MATH = re.compile(r"\$\$.+?\$\$|\$[^$\n]+?\$|\\\[.+?\\\]|\\\(.+?\\\)", re.DOTALL)


def plain(md: str) -> str:
    """Markdown -> comparable text: math removed (scored separately), markup stripped."""
    return strip_markup(_MATH.sub(" ", md))


def ned(gt: str, pred: str) -> float:
    """1 − normalised Levenshtein distance of the squashed strings (1.0 = identical; empty pred for a non-empty
    GT = 0.0; both empty = 1.0)."""
    a, b = squash(gt), squash(pred)
    if not a and not b:
        return 1.0
    if not b or not a:
        return 0.0
    return 1.0 - Levenshtein.normalized_distance(a, b)


def cer(gt: str, pred: str) -> float:
    a, b = squash(gt), squash(pred)
    if not a:
        return 0.0 if not b else 1.0
    return Levenshtein.distance(a, b) / len(a)


def page_scores(gt: dict, sections: dict[int, str]) -> dict[int, dict]:
    """Per GT page: {ned, cer, dropped}. A page missing from `sections` is an empty prediction."""
    out: dict[int, dict] = {}
    blank = set(gt.get("blank_pages") or [])
    for k, text in gt["page_text"].items():
        p = int(k)
        pred = plain(sections.get(p, ""))
        nonspace = sum(1 for c in pred if not c.isspace())
        out[p] = {"ned": round(ned(text, pred), 4), "cer": round(cer(text, pred), 4),
                  "dropped": p not in blank and bool(squash(text)) and nonspace < DROPPED_SECTION_MAX}
    return out


def mean_ned(scores: dict[int, dict], gt: dict) -> float:
    pages = [p for p in scores if p not in set(gt.get("blank_pages") or [])]
    return round(sum(scores[p]["ned"] for p in pages) / len(pages), 4) if pages else 1.0


def recovery_rate(defect_pages, scores: dict[int, dict], threshold: float = RECOVERED_NED) -> float | None:
    pages = [p for p in defect_pages if p in scores]
    if not pages:
        return None
    return sum(1 for p in pages if scores[p]["ned"] >= threshold) / len(pages)


def dropped_rate(gt: dict, scores: dict[int, dict]) -> float:
    blank = set(gt.get("blank_pages") or [])
    pages = [p for p in range(1, int(gt["pages"]) + 1) if p not in blank]
    if not pages:
        return 0.0
    return sum(1 for p in pages if scores.get(p, {"dropped": True})["dropped"]) / len(pages)


# ---- tables: TEDS (PubTabNet) with an in-repo Zhang–Shasha tree edit distance

class _Node:
    __slots__ = ("children", "colspan", "tag", "text")

    def __init__(self, tag: str, text: str = "", colspan: int = 1, children=None):
        self.tag, self.text, self.colspan, self.children = tag, text, colspan, children or []


def _tree(rows: list[list[tuple[str, int]]]) -> _Node:
    return _Node("table", children=[_Node("tr", children=[_Node("td", squash(t), cs) for t, cs in row])
                                    for row in rows])


def _gt_rows(cells: list[list[str]], spans: list[dict] | None) -> list[list[tuple[str, int]]]:
    span = {(s["row"], s["col"]): s["colspan"] for s in spans or []}
    covered = {(s["row"], s["col"] + k) for s in spans or [] for k in range(1, s["colspan"])}
    return [[(v, span.get((r, c), 1)) for c, v in enumerate(row) if (r, c) not in covered]
            for r, row in enumerate(cells)]


class _HtmlTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[list[tuple[str, int]]] = []
        self._cell: list[str] | None = None
        self._span = 1
        self.done = False
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "table":
            self.depth += 1
        elif tag == "tr" and self.depth:
            self.rows.append([])
        elif tag in ("td", "th") and self.depth and self.rows:
            self._cell, self._span = [], int(dict(attrs).get("colspan") or 1)

    def handle_endtag(self, tag):
        if self.done:
            return
        if tag in ("td", "th") and self._cell is not None:
            self.rows[-1].append(("".join(self._cell).strip(), self._span))
            self._cell = None
        elif tag == "table" and self.depth:
            self.depth -= 1
            self.done = self.depth == 0

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


_GFM_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _pred_rows(section: str) -> list[list[tuple[str, int]]] | None:
    """Rows of the first table in a Markdown section (GFM or HTML), whichever comes first."""
    h = section.lower().find("<table")
    lines = section.split("\n")
    g_start = None
    for i in range(len(lines) - 1):
        if lines[i].strip().startswith("|") and _GFM_SEP.match(lines[i + 1]):
            g_start = i
            break
    g_pos = sum(len(x) + 1 for x in lines[:g_start]) if g_start is not None else None
    if h != -1 and (g_pos is None or h < g_pos):
        p = _HtmlTable()
        p.feed(section[h:])
        return p.rows or None
    if g_start is None:
        return None
    rows = []
    for ln in lines[g_start:]:
        s = ln.strip()
        if not s.startswith("|"):
            break
        if _GFM_SEP.match(s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        rows.append([(c, 1) for c in cells])
    return rows or None


def _postorder(root: _Node):
    nodes, lmd = [], []

    def walk(n):
        first = None
        for c in n.children:
            leaf = walk(c)
            if first is None:
                first = leaf
        idx = len(nodes)
        nodes.append(n)
        lmd.append(first if first is not None else idx)
        return lmd[idx]
    walk(root)
    return nodes, lmd


def _rename(a: _Node, b: _Node, structure_only: bool) -> float:
    if a.tag != b.tag or a.colspan != b.colspan:
        return 1.0
    if a.tag != "td" or structure_only:
        return 0.0
    if not a.text and not b.text:
        return 0.0
    return Levenshtein.normalized_distance(a.text, b.text)


def tree_edit_distance(t1: _Node, t2: _Node, structure_only: bool = False) -> float:
    """Zhang–Shasha; insert/delete cost 1, rename cost `_rename`."""
    n1, l1 = _postorder(t1)
    n2, l2 = _postorder(t2)
    kr1 = sorted({max(i for i in range(len(n1)) if l1[i] == l) for l in set(l1)})
    kr2 = sorted({max(j for j in range(len(n2)) if l2[j] == l) for l in set(l2)})
    td = [[0.0] * len(n2) for _ in range(len(n1))]
    for i in kr1:
        for j in kr2:
            li, lj = l1[i], l2[j]
            m, n = i - li + 2, j - lj + 2
            fd = [[0.0] * n for _ in range(m)]
            for x in range(1, m):
                fd[x][0] = fd[x - 1][0] + 1
            for y in range(1, n):
                fd[0][y] = fd[0][y - 1] + 1
            for x in range(1, m):
                for y in range(1, n):
                    ii, jj = li + x - 1, lj + y - 1
                    if l1[ii] == li and l2[jj] == lj:
                        fd[x][y] = min(fd[x - 1][y] + 1, fd[x][y - 1] + 1,
                                       fd[x - 1][y - 1] + _rename(n1[ii], n2[jj], structure_only))
                        td[ii][jj] = fd[x][y]
                    else:
                        fd[x][y] = min(fd[x - 1][y] + 1, fd[x][y - 1] + 1,
                                       fd[l1[ii] - li][l2[jj] - lj] + td[ii][jj])
    return td[-1][-1]


def _size(n: _Node) -> int:
    return 1 + sum(_size(c) for c in n.children)


def table_teds(gt_cells: list[list[str]], pred_section: str, structure_only: bool = False,
               spans: list[dict] | None = None) -> float:
    """TEDS (or TEDS-S with structure_only) of the first table in `pred_section` against the GT cell matrix; 0.0
    when the section has no table."""
    rows = _pred_rows(pred_section)
    if not rows:
        return 0.0
    a, b = _tree(_gt_rows(gt_cells, spans)), _tree(rows)
    return max(0.0, 1.0 - tree_edit_distance(a, b, structure_only) / max(_size(a), _size(b)))


def tables_teds(gt_tables: list[dict], pred_section: str, structure_only: bool = False) -> list[float]:
    """Each GT table of a page against the page's tables in order (the k-th GT table vs the k-th predicted one)."""
    parts = _split_tables(pred_section)
    out = []
    for k, t in enumerate(gt_tables):
        out.append(table_teds(t["cells"], parts[k], structure_only, t.get("spans")) if k < len(parts) else 0.0)
    return out


def _split_tables(section: str) -> list[str]:
    """The section cut so that each piece starts at one table (GFM block or <table>)."""
    starts = [m.start() for m in re.finditer(r"(?im)^<table|<table", section)]
    lines = section.split("\n")
    pos = 0
    in_gfm = False
    for i, ln in enumerate(lines):
        is_row = ln.strip().startswith("|")
        if is_row and not in_gfm and i + 1 < len(lines) and _GFM_SEP.match(lines[i + 1]):
            starts.append(pos)
        in_gfm = is_row
        pos += len(ln) + 1
    starts = sorted(set(starts))
    return [section[s:(starts[k + 1] if k + 1 < len(starts) else len(section))] for k, s in enumerate(starts)]


# ---- formulas

_LEFT_RIGHT = re.compile(r"\\(?:left|right)\s*(?:\\[{}|]|[()\[\]|.]|\\[a-zA-Z]+)?")
_SPACING = re.compile(r"\\[,;:! ]|\\quad|\\qquad")


def norm_latex(s: str) -> str:
    s = _LEFT_RIGHT.sub("", s)
    s = _SPACING.sub("", s)
    s = s.replace("\\displaystyle", "").replace("\\mathrm", "").replace("\\operatorname", "")
    return re.sub(r"\s+", "", s)


def formulas_in(section: str) -> list[str]:
    out = []
    for m in _MATH.finditer(section):
        t = m.group(0)
        for a, b in (("$$", "$$"), ("$", "$"), ("\\[", "\\]"), ("\\(", "\\)")):
            if t.startswith(a) and t.endswith(b) and len(t) >= len(a) + len(b):
                out.append(t[len(a):len(t) - len(b)])
                break
    return out


def formula_ned(gt_latex: list[str], section: str) -> float:
    """Each GT formula matched greedily to its best unused predicted formula (NED of normalised LaTeX); averaged.
    0.0 when the section has no math."""
    preds = [norm_latex(p) for p in formulas_in(section)]
    if not gt_latex:
        return 1.0
    total, used = 0.0, set()
    for g in gt_latex:
        g = norm_latex(g)
        best, best_i = 0.0, None
        for i, p in enumerate(preds):
            if i in used:
                continue
            v = 1.0 - Levenshtein.normalized_distance(g, p) if (g or p) else 1.0
            if v > best:
                best, best_i = v, i
        if best_i is not None:
            used.add(best_i)
        total += best
    return total / len(gt_latex)


# ---- rank statistics

def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman's rho (average ranks for ties); 0.0 when either side is constant."""
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("need two equal-length lists of at least 2 values")
    rx, ry = _ranks(list(xs)), _ranks(list(ys))
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if not vx or not vy:
        return 0.0
    return cov / (vx * vy) ** 0.5


def precision_recall(pred: set, gt: set) -> tuple[float, float]:
    tp = len(set(pred) & set(gt))
    p = tp / len(pred) if pred else 1.0
    r = tp / len(gt) if gt else 1.0
    return p, r
