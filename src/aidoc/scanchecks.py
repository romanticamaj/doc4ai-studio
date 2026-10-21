"""Page checks for scanned pages — no text layer needed (ticket T-002).

The layer-based page checks (`quality.page_checks`) need the PDF text layer; on a scan they see nothing, so a page
an engine left empty, or whose text it put under the neighbouring page's marker, went unflagged. Here a page's
image says how much text it holds:

- `page_ink()` renders each page without a text layer in grey at INK_DPI and counts ink (pixels darker than
  INK_DARK × the page's own paper tone, so yellowed paper is not ink). The thickness of the text bands in the
  dominant direction (rows for horizontal text, columns for vertical) is the glyph size s, and
  cells = ink / (s² · GLYPH_FILL) estimates the number of characters — type size cancels out, so a title page in
  96 pt has few cells and a dense 9 pt page many.
- `scan_page_checks()` compares each section's characters with its cells:
  - page_empty_scan: the section has < EMPTY_SECTION_MAX characters while the page holds at least CELLS_MIN cells
    and EMPTY_CELLS_REL of the document's median — the engine lost a page that has text;
  - page_oversized (on page i) + page_misaligned (on its neighbours): with r = the document's median
    characters-per-cell, page i holds ≥ OVER × r·cells_i characters, a run of adjacent pages holds ≤ UNDER of
    theirs, and the excess of i matches their deficit within BALANCE — the neighbours' text went to page i.
  Every entry carries metrics["scan"] = True: re-OCR of a scan page image cannot move text across pages, so the
  pipeline repairs these pages (both sides of a misattribution) like page_dropped.

Thresholds are frozen (bench/scan_calib.py on the synthetic Tier A scans + the design label pages, 2026-10-22,
ticket T-002; pinned by tests/unit/test_scan_checks.py::test_thresholds_are_frozen); not to be tuned on the
real-sample regression set."""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from aidoc import pagemap

INK_DPI = 72
INK_MARGIN = 0.03            # scanner borders, punch holes and gutters at the page edge are not text
INK_PAPER_PCT = 0.90         # the paper tone: the 90th-percentile grey of the page
INK_DARK = 0.55              # ink = darker than INK_DARK × the paper tone
BAND_MIN_SHARE = 0.004       # a row / column is in a text band when this share of its pixels is ink
BAND_MIN_PX = 3              # thinner bands are rules or specks
BAND_MAX_SHARE = 0.15        # wider bands are merged text blocks, not lines
PITCH_MIN_PX = 4             # the shortest line pitch considered (pixels at INK_DPI)
PITCH_MAX_SHARE = 0.15       # ... and the longest, as a share of the profile
PITCH_MIN_STRENGTH = 0.25    # autocorrelation of a periodic (multi-line) text block
LINES_MIN = 3                # lines needed to read a pitch
STRIPS = 6                   # profiles are measured in this many strips (skew)
PITCH_PEAK_REL = 0.5         # the first autocorrelation peak this close to the strongest is the pitch
LINE_GAP = 1.6               # line pitch ≈ glyph band × LINE_GAP (one-line pages)
GLYPH_FILL = 0.12            # ink share of a pitch² cell (cells ≈ characters on the synthetic scans)
SCAN_LAYER_MAX = 20          # = verify.TEXT_LAYER_MIN: a page with fewer text-layer characters is a scan page
EMPTY_SECTION_MAX = 20       # = quality.DROPPED_SECTION_MAX
CELLS_MIN = 20               # a page "has text" when it holds this many glyph cells
INK_TEXT_MAX = 0.35          # ... and no more ink than this: above it the page is a picture (a cover photo)
EMPTY_CELLS_REL = 0.10       # ... and at least this share of the document's median cells (a stamp-only page is not)
DENSITY_MIN_PAGES = 3        # pages needed to estimate characters-per-cell
OVER = 1.5
UNDER = 0.5
EXCESS_MIN = 40              # characters
BALANCE = (0.5, 2.0)


@dataclass(frozen=True)
class PageInk:
    ink: float               # ink share of the page (margins excluded)
    glyph: float             # line pitch in pixels at INK_DPI
    cells: float             # estimated characters


def _bands(profile: list[int], line_px: int) -> list[tuple[int, int]]:
    """(thickness, ink) of each band of consecutive rows / columns holding ink."""
    t = max(1, int(BAND_MIN_SHARE * line_px))
    out, run, ink = [], 0, 0
    for v in list(profile) + [0]:
        if v >= t:
            run += 1
            ink += v
        elif run:
            if run >= BAND_MIN_PX:
                out.append((run, ink))
            run, ink = 0, 0
    return out


def _autocorr(profile: list[int], max_lag: int) -> list[float]:
    n = len(profile)
    ac = [0.0] * (max_lag + 2)
    if n < 3 * PITCH_MIN_PX:
        return ac
    mean = sum(profile) / n
    x = [v - mean for v in profile]
    var = sum(v * v for v in x)
    if var <= 0:
        return ac
    for lag in range(PITCH_MIN_PX - 1, min(max_lag + 2, n)):
        ac[lag] = sum(x[i] * x[i + lag] for i in range(n - lag)) / var
    return ac


def _pitch(profiles: list[list[int]], max_lag: int, min_lag: int = 0) -> tuple[int, float]:
    """(lag, strength): the period of row / column ink profiles — the line pitch of a text block. The
    autocorrelations of the strips' centred profiles are averaged; the first strong local maximum between
    PITCH_MIN_PX (or min_lag) and max_lag is the pitch."""
    acs = [_autocorr(p, max_lag) for p in profiles if p]
    if not acs:
        return 0, 0.0
    ac = [sum(a[i] for a in acs) / len(acs) for i in range(max_lag + 2)]
    peaks = [(lag, ac[lag]) for lag in range(max(PITCH_MIN_PX, min_lag), max_lag + 1)
             if ac[lag] > ac[lag - 1] and ac[lag] >= ac[lag + 1]]
    if not peaks:
        return 0, 0.0
    best = max(v for _, v in peaks)
    # the line pitch is the first strong peak; later ones are its multiples (paragraph rhythm can make one of
    # those the strongest: the synthetic scans read 5× the pitch on 15 pages)
    return next((lag, v) for lag, v in peaks if v >= PITCH_PEAK_REL * best)


def _strips(lo: int, hi: int, k: int) -> list[tuple[int, int]]:
    step = max(1, (hi - lo) // k)
    return [(lo + i * step, min(hi, lo + (i + 1) * step)) for i in range(k) if lo + i * step < hi]


def measure(page) -> PageInk:
    """Ink, glyph pitch and glyph cells of one PyMuPDF page."""
    import pymupdf
    pix = page.get_pixmap(dpi=INK_DPI, colorspace=pymupdf.csGRAY, alpha=False)
    w, h, buf = pix.width, pix.height, pix.samples
    hist = sorted((v[0], n) for v, n in pix.color_count(colors=True).items())
    total, acc, paper = w * h, 0, 255
    for v, n in hist:
        acc += n
        if acc >= INK_PAPER_PCT * total:
            paper = v
            break
    thr = int(paper * INK_DARK)
    light = bytes(range(thr, 256))
    x0, x1 = int(w * INK_MARGIN), w - int(w * INK_MARGIN)
    y0, y1 = int(h * INK_MARGIN), h - int(h * INK_MARGIN)
    if x1 <= x0 or y1 <= y0 or thr <= 0:
        return PageInk(0.0, 0.0, 0.0)
    # profiles in STRIPS narrow strips: on a skewed page a full-width row profile smears the lines together, a
    # strip's drifts only a few pixels (the synthetic scans' `skew` pages read 5× the pitch without strips)
    row_strips = [[len(buf[y * w + a:y * w + b].translate(None, light)) for y in range(y0, y1)]
                  for a, b in _strips(x0, x1, STRIPS)]
    col_strips = [[len(buf[a * w + x:b * w + x:w].translate(None, light)) for x in range(x0, x1)]
                  for a, b in _strips(y0, y1, STRIPS)]
    ink_px = sum(sum(p) for p in row_strips)
    area = (x1 - x0) * (y1 - y0)
    if ink_px == 0:
        return PageInk(0.0, 0.0, 0.0)
    # glyph pitch. Text lines are the bands that run along the text (rows for horizontal text, columns for
    # vertical): long bands (much ink per row of the band); across them a band is one glyph. With LINES_MIN lines or
    # more, the line pitch is the profiles' period (at least the band thickness: shorter periods are strokes inside
    # glyphs); with fewer, the band thickness × LINE_GAP.
    cands = []
    for strips, span_len, length in ((row_strips, (x1 - x0) // STRIPS, y1 - y0),
                                     (col_strips, (y1 - y0) // STRIPS, x1 - x0)):
        bands = [(t, k) for prof in strips for t, k in _bands(prof, span_len) if t <= BAND_MAX_SHARE * length]
        if bands:
            cands.append((statistics.median(k / t for t, k in bands), strips, bands, length))
    if not cands:
        return PageInk(round(ink_px / area, 5), 0.0, 0.0)
    _, strips, bands, length = max(cands, key=lambda c: c[0])
    thick = float(statistics.median(t for t, _ in bands))
    g = thick * LINE_GAP
    if len(bands) >= LINES_MIN * len(strips):
        lag, strength = _pitch(strips, int(PITCH_MAX_SHARE * length), min_lag=int(thick))
        if strength >= PITCH_MIN_STRENGTH:
            g = float(lag)
    return PageInk(round(ink_px / area, 5), g, round(ink_px / (g * g * GLYPH_FILL), 1))


def page_ink(pdf: Path, *, layers: list[str] | None = None) -> dict[int, PageInk | None]:
    """{page: PageInk} for every page without a text layer, None for pages that have one (the layer-based checks
    own those). `layers`: the squashed text layers when the caller has them."""
    import pymupdf
    out: dict[int, PageInk | None] = {}
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc, 1):
            layer = layers[i - 1] if layers is not None and i - 1 < len(layers) else pagemap.squash(page.get_text())
            out[i] = measure(page) if len(layer) < SCAN_LAYER_MAX else None
    return out


_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def section_chars(section: str) -> int:
    return len(pagemap.squash(pagemap.strip_tags(_COMMENT.sub(" ", _IMG.sub(" ", section)))))


def scan_page_checks(md: str, ink: dict[int, PageInk | None], blank_pages=()) -> list[dict]:
    """Entries {page, reasons, metrics} for scan pages whose section does not match the page image (see the module
    docstring). Pages with a text layer (ink None) and blank pages are not judged."""
    _, secs = pagemap.split_pages(md)
    blank = set(blank_pages)
    scan = {p: v for p, v in ink.items() if v is not None and p not in blank}
    chars = {p: section_chars(secs.get(p, "")) for p in scan}
    textful = sorted(p for p, v in scan.items() if v.cells >= CELLS_MIN and v.ink <= INK_TEXT_MAX)
    if not textful:
        return []
    med_cells = statistics.median(scan[p].cells for p in textful)
    dense = [chars[p] / scan[p].cells for p in textful if chars[p] >= EMPTY_SECTION_MAX]
    r = statistics.median(dense) if len(dense) >= DENSITY_MIN_PAGES else None
    entries: dict[int, dict] = {}

    def metrics(p, **kw):
        m = {"scan": True, "section_chars": chars[p], "cells": scan[p].cells, "ink": scan[p].ink}
        if r is not None:
            m["expected_chars"] = round(r * scan[p].cells)
        return {**m, **kw}
    if r is not None:
        exp = {p: r * scan[p].cells for p in textful}
        tf = set(textful)
        for i in textful:
            if i in entries or chars[i] < OVER * exp[i] or chars[i] - exp[i] < EXCESS_MIN:
                continue
            run = []
            for step in (1, -1):
                j = i + step
                while j in tf and j not in entries and chars[j] <= UNDER * exp[j]:
                    run.append(j)
                    j += step
            deficit = sum(exp[j] - chars[j] for j in run)
            if run and deficit > 0 and BALANCE[0] <= (chars[i] - exp[i]) / deficit <= BALANCE[1]:
                entries[i] = {"page": i, "reasons": ["page_oversized"], "metrics": metrics(i, pair=sorted(run))}
                for j in run:
                    entries[j] = {"page": j, "reasons": ["page_misaligned"], "metrics": metrics(j, pair=[i])}
    for p in textful:
        if p not in entries and chars[p] < EMPTY_SECTION_MAX and scan[p].cells >= EMPTY_CELLS_REL * med_cells:
            entries[p] = {"page": p, "reasons": ["page_empty_scan"], "metrics": metrics(p)}
    return [entries[p] for p in sorted(entries)]
