"""Page sections, page-map coverage, alignment spot check and page splicing (spec 2026-10-01 §6, §7, §8.2).

Page boundaries come from the engines' structure (runners); this module never searches markdown text to *find*
page boundaries of our own outputs — it only reads the `<!-- page: N -->` markers and checks them against the PDF
text layer. The one exception is `infer_pages()` (spec 2026-10-05 verify §2.2): third-party Markdown without any
usable page markers, anchored by the same unique text-layer probes as the spot check.
"""
from __future__ import annotations

import bisect
import itertools
import random
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

MARKER = re.compile(r"<!-- page: (\d+) -->")
MISSING_LIST_MAX = 200
MISPLACED_LIST_MAX = 10
_FIND_MAX = 5
_CANDIDATE_STARTS = 9


def _chunks(md: str) -> tuple[str, list[tuple[int, int, int]]]:
    """(preamble, [(page, body_start, body_end)]) in document order."""
    ms = list(MARKER.finditer(md))
    if not ms:
        return md, []
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(md)
        out.append((int(m.group(1)), m.end(), end))
    return md[:ms[0].start()], out


def split_pages(md: str) -> tuple[str, dict[int, str]]:
    """(text before the first marker, {page: section}); a page that appears twice gets its sections concatenated."""
    pre, chunks = _chunks(md)
    secs: dict[int, str] = {}
    for page, a, b in chunks:
        secs[page] = secs.get(page, "") + md[a:b]
    return pre, secs


def page_map(md: str, expected: int | None, method: str | None) -> dict | None:
    if expected is None:
        return None
    found_set = {int(n) for n in MARKER.findall(md) if 1 <= int(n) <= expected}
    missing = [p for p in range(1, expected + 1) if p not in found_set]
    out: dict = {"expected": expected, "found": len(found_set),
                 "coverage": round(len(found_set) / expected, 4) if expected else 1.0,
                 "missing": missing[:MISSING_LIST_MAX], "method": method}
    if len(missing) > MISSING_LIST_MAX:
        out["missing_truncated"] = True
    return out


def invalid_markers(md: str, expected: int) -> int:
    """Markers that break "one marker per page, in order": a page outside 1..expected, or a page number not greater
    than the previous marker's (a duplicate or a decrease)."""
    bad, prev = 0, 0
    for m in MARKER.finditer(md):
        n = int(m.group(1))
        if not 1 <= n <= expected or n <= prev:
            bad += 1
        else:
            prev = n
    return bad


def squash_with_map(s: str) -> tuple[str, list[int]]:
    """`squash(s)` plus, for every squashed character, its offset in `s` (so a squashed position maps back).
    Character-wise NFKC; soft hyphens and line-break hyphens are not alphanumeric, so dropping them is implicit."""
    out: list[str] = []
    pos: list[int] = []
    for i, c in enumerate(s):
        if c.isascii():
            if c.isalnum():
                out.append(c.lower())
                pos.append(i)
            continue
        for ch in unicodedata.normalize("NFKC", c).lower():
            if ch.isalnum():
                out.append(ch)
                pos.append(i)
    return "".join(out), pos


# HTML that engines write into Markdown (tables, line breaks, sub/superscripts, emphasis, links, images) — only these:
# `<LinearLayout ...>` in an XML code listing is page text (verifier round 2: a generic tag pattern ate code pages)
_HTML_TAG = re.compile(r"</?(?:table|thead|tbody|tfoot|tr|td|th|caption|colgroup|col|br|hr|p|div|span|sub|sup|b|i|u|"
                       r"em|strong|s|del|ins|mark|small|big|code|pre|a|img|ul|ol|li|dl|dt|dd|h[1-6]|font|center|"
                       r"figure|figcaption|blockquote|details|summary)(?:\s[^<>]*)?/?>", re.IGNORECASE)
_LINK_URL = re.compile(r"\]\([^)\s]*(?:\s+\"[^\"]*\")?\)")


def strip_tags(s: str) -> str:
    """HTML tags engines emit (`<td>a</td><td>b</td>` -> ` a  b `), character entities (`&lt;` -> `<`) and Markdown
    link targets (`[t](url)` -> `[t]`) removed: none of them is page text."""
    import html
    return html.unescape(_HTML_TAG.sub(" ", _LINK_URL.sub("]", s)))


def squash(s: str) -> str:
    """NFKC, drop soft hyphens and line-break hyphenation, lower-case, keep only alphanumerics (any script)."""
    s = unicodedata.normalize("NFKC", s).replace("\u00ad", "").replace("-\n", "")
    return "".join(c for c in s.lower() if c.isalnum())


def pdf_text_layers(pdf: Path) -> list[str]:
    import pymupdf
    with pymupdf.open(pdf) as doc:
        return [squash(page.get_text()) for page in doc]


# ---- page content without running headers / footers / page numbers (for per-page coverage checks)

FURNITURE_EDGE_LINES = 3          # a running header / footer sits among the first or last lines of a page
FURNITURE_MIN_SHARE = 0.2         # ... and repeats on at least this share of the pages (and on 3 pages)
PROSE_LINE_MIN = 20               # shorter lines (diagram labels, table cells, page furniture) are not prose
FURNITURE_MAX_SHARE = 0.5         # furniture never takes more than half of a page's text
_DIGITS = re.compile(r"\d+")


def _furniture_key(line: str) -> str:
    return _DIGITS.sub("#", squash(line))


def _edges(lines: list[str]) -> list[int]:
    idx = [i for i, ln in enumerate(lines) if squash(ln)]
    return sorted(set(idx[:FURNITURE_EDGE_LINES] + idx[-FURNITURE_EDGE_LINES:]))


def content_layers(pages_lines: list[list[str]], prose_only: bool = False,
                   skip: list[set[int]] | None = None) -> list[str]:
    """Squashed page text without its running header / footer and page number: lines among the first / last
    FURNITURE_EDGE_LINES of a page whose digit-normalised form recurs at those positions on many pages, plus edge
    lines that are only a number. A figure page whose text is just the header, the page number and a caption must
    not look like a page full of text. `prose_only` also leaves out lines shorter than PROSE_LINE_MIN and the lines
    in `skip` (per page: lines inside a picture - labels and callouts on a screenshot or figure)."""
    counts: dict[str, int] = {}
    for lines in pages_lines:
        for k in {_furniture_key(lines[i]) for i in _edges(lines)}:
            counts[k] = counts.get(k, 0) + 1
    need = max(3, FURNITURE_MIN_SHARE * len(pages_lines))
    furniture = {k for k, c in counts.items() if c >= need and k}
    drops = [{i for i in _edges(lines) if (k := _furniture_key(lines[i])) in furniture or k == "#"}
             for lines in pages_lines]
    total = sum(len(squash(ln)) for lines in pages_lines for ln in lines)
    removed = sum(len(squash(lines[i])) for lines, d in zip(pages_lines, drops, strict=True) for i in d)
    if total and removed > FURNITURE_MAX_SHARE * total:
        # a document that repeats its whole text (synthetic / boilerplate pages): no furniture. Judged on the whole
        # document, not per page — a page holding only its header and footer has no content (verifier round 2: the
        # per-page cap made header/footer-only and caption-only pages look full of text)
        drops = [set() for _ in pages_lines]
    out = []
    for n, (lines, drop) in enumerate(zip(pages_lines, drops, strict=True)):
        fig = skip[n] if skip is not None and n < len(skip) else set()
        keep = []
        for i, ln in enumerate(lines):
            if i in drop or (prose_only and i in fig):
                continue
            sq = squash(ln)
            if prose_only and len(sq) < PROSE_LINE_MIN:
                continue
            keep.append(sq)
        out.append("".join(keep))
    return out


TRUSTED_LAYER_MIN = 120           # squashed chars of healthy-font text a broken-font page needs to be checked
FIGURE_AREA_MIN = 0.02            # a picture covering at least this share of the page is a figure region


class PageLayers(NamedTuple):
    full: list[str]                # squashed text layer per page
    prose: list[str]               # ... without furniture, short lines and lines inside pictures
    body: list[str]                # ... without furniture
    untrusted: list[int]           # pages whose layer is not their text (broken fonts, too little healthy text)


class PageText(NamedTuple):
    lines: list[list[str]]
    untrusted: list[int]
    figure: list[set[int]]         # per page: indices of the lines inside a picture


def _in_rects(bbox, rects) -> bool:
    x, y = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in rects)


def page_lines(doc) -> PageText:
    """Text lines per page, untrusted pages, figure lines. A page that uses a font without a Unicode mapping
    (`probe.font_status`) keeps only its healthy-font text: Head First marks hundreds of pages "broken" because of
    handwriting annotation fonts, while its body text and code extract fine (verifier round 2). With less than
    TRUSTED_LAYER_MIN chars of healthy text the raw text is kept and the page is untrusted. Lines whose centre lies
    in a picture of at least FIGURE_AREA_MIN of the page are figure lines: labels and callouts on a figure kept as a
    picture are not page prose (verifier round 3 policy); text drawn as vectors cannot be told apart that cheaply."""
    import pymupdf

    from aidoc.probe import font_status, span_is_broken
    broken = font_status(doc)                   # per page: a font name can be broken on one page, healthy on another
    out: list[list[str]] = []
    untrusted: list[int] = []
    figure: list[set[int]] = []
    for i in range(doc.page_count):
        page = doc[i]
        area = abs(page.rect.width * page.rect.height) or 1.0
        rects = [im["bbox"] for im in page.get_image_info()
                 if abs((im["bbox"][2] - im["bbox"][0]) * (im["bbox"][3] - im["bbox"][1])) >= FIGURE_AREA_MIN * area]
        bad, mixed = broken.get(i + 1, (set(), set()))
        raw, healthy, fig = [], [], set()
        for blk in page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)["blocks"]:
            for ln in blk.get("lines", []):
                raw.append("".join(sp["text"] for sp in ln["spans"]))
                if rects and _in_rects(ln["bbox"], rects):
                    fig.add(len(raw) - 1)
                healthy.append("".join(sp["text"] for sp in ln["spans"] if not span_is_broken(sp, bad, mixed)))
        lines = raw
        if bad:
            if len(squash("\n".join(healthy))) >= TRUSTED_LAYER_MIN:
                lines = healthy
            else:
                untrusted.append(i + 1)
        out.append(lines)
        figure.append(fig)
    return PageText(out, untrusted, figure)


def layers_from_lines(lines: list[list[str]], untrusted: list[int], figure: list[set[int]] | None = None) -> PageLayers:
    return PageLayers([squash("\n".join(ln)) for ln in lines], content_layers(lines, prose_only=True, skip=figure),
                      content_layers(lines), untrusted)


def pdf_layers(pdf: Path) -> PageLayers:
    """(text layers, prose layers, body layers, untrusted pages) in one pass. The body layer drops the running header /
    footer / page number; the prose layer also drops every line shorter than PROSE_LINE_MIN (labels inside drawn
    figures, table cells) and the lines inside pictures: an engine that keeps a diagram as a picture has not cut the
    page's prose — but a full-page table it kept as a picture is still a dropped page (verifier round 2, finding A:
    dropped uses the body layer)."""
    import pymupdf
    with pymupdf.open(pdf) as doc:
        return layers_from_lines(*page_lines(doc))


def page_homes(md: str, layers: list[str], *, exclude: Iterable[int] = (), win: int = 24, k: int = 3,
               min_chars: int = 120) -> dict[int, dict]:
    """Where every page's text sits in the Markdown: for each page with probes (unique in the whole text layer,
    `_probe_spans`), how many were found, how many in the page's own section, and which other sections hold them.
    Every page is probed (unlike the sampled spot check)."""
    n = len(layers)
    excl = set(exclude)
    whole = "\0".join(layers)
    pre, chunks = _chunks(md)
    parts, starts, owners = [squash(strip_tags(pre))], [0], [None]
    pos = len(parts[0])
    for page, a, b in chunks:
        sq = squash(strip_tags(md[a:b]))         # probes never matched across <td> cells (verifier round 2, B)
        starts.append(pos)
        owners.append(page)
        parts.append(sq)
        pos += len(sq)
    t = "".join(parts)

    def owner_at(i: int):
        return owners[bisect.bisect_right(starts, i) - 1]

    out: dict[int, dict] = {}
    for p in range(1, n + 1):
        if p in excl or len(layers[p - 1]) < min_chars:
            continue
        spans = _probe_spans(layers[p - 1], p, whole, win, k)
        if not spans:
            continue
        found = home = 0
        hosts: dict[int, int] = {}
        dup: dict[int, int] = {}                   # other sections holding a probe that is ALSO in the page's own
        for _, probe in spans:
            hit_pages = []
            i = t.find(probe)
            while i != -1 and len(hit_pages) < _FIND_MAX:
                hit_pages.append(owner_at(i))
                i = t.find(probe, i + 1)
            if not hit_pages:
                continue
            found += 1
            if p in hit_pages:
                home += 1
            for h in set(hit_pages):
                if h is not None and h != p:
                    hosts[h] = hosts.get(h, 0) + 1
                    if p in hit_pages:
                        dup[h] = dup.get(h, 0) + 1
        out[p] = {"probes": len(spans), "found": found, "home": home, "hosts": dict(sorted(hosts.items())),
                  "duplicated_in": dict(sorted(dup.items()))}
    return out


def _even_sample(items: list[int], k: int) -> list[int]:
    if k >= len(items):
        return list(items)
    return [items[(i * len(items)) // k] for i in range(k)]


def _probe_spans(text: str, page_no: int, whole: str, win: int, k: int) -> list[tuple[int, str]]:
    """[(offset in the page's squashed layer, probe)]: seeded by the page number, 20 %-80 % of the page, unique in
    the whole PDF text layer (spec 2026-10-01 §7; shared by spot_check and infer_pages)."""
    lo, hi = int(len(text) * 0.2), int(len(text) * 0.8) - win
    if hi <= lo:
        return []
    rng = random.Random(page_no)
    out: list[tuple[int, str]] = []
    for _ in range(_CANDIDATE_STARTS):
        start = rng.randint(lo, hi)
        probe = text[start:start + win]
        if len(probe) == win and all(probe != q for _, q in out) and whole.count(probe) == 1:
            out.append((start, probe))
            if len(out) >= k:
                break
    return out


def _probes(text: str, page_no: int, whole: str, win: int, k: int) -> list[str]:
    return [probe for _, probe in _probe_spans(text, page_no, whole, win, k)]


def spot_check(md: str, pdf: Path, *, sample: int = 30, exclude: Iterable[int] = (), win: int = 24, k: int = 3,
               min_chars: int = 120, layers: list[str] | None = None) -> dict:
    """Spec §7 (v3): probes unique in the whole PDF text layer must land in their own page's section."""
    layers = pdf_text_layers(pdf) if layers is None else layers
    n = len(layers)
    excl = {p for p in exclude if 1 <= p <= n}
    whole = "\0".join(layers)
    # squashed markdown sections in document order + section start offsets
    pre, chunks = _chunks(md)
    parts, starts, owners = [squash(pre)], [0], [None]
    pos = len(parts[0])
    for page, a, b in chunks:
        sq = squash(md[a:b])
        starts.append(pos)
        owners.append(page)
        parts.append(sq)
        pos += len(sq)
    t = "".join(parts)

    def owner_at(i: int) -> int | None:
        return owners[bisect.bisect_right(starts, i) - 1]

    cands = [p for p in range(1, n + 1) if p not in excl and len(layers[p - 1]) >= min_chars]
    picked = _even_sample(cands, sample)
    decidable = aligned = 0
    misplaced: list[int] = []
    run = max_run = 0                                  # consecutive misplaced decidable pages (a local shift)
    for p in picked:
        found = home = 0
        for probe in _probes(layers[p - 1], p, whole, win, k):
            pages_hit = []
            i = t.find(probe)
            while i != -1 and len(pages_hit) < _FIND_MAX:
                pages_hit.append(owner_at(i))
                i = t.find(probe, i + 1)
            if pages_hit:
                found += 1
                home += p in pages_hit
        if not found:
            continue                                   # the engine did not output this text: undecidable
        decidable += 1
        if home * 2 > found:
            aligned += 1
            run = 0
        else:
            misplaced.append(p)
            run += 1
            max_run = max(max_run, run)
    return {"sampled": len(picked), "decidable": decidable, "aligned": aligned,
            "ratio": round(aligned / decidable, 4) if decidable else None,
            "misplaced": misplaced[:MISPLACED_LIST_MAX], "max_misplaced_run": max_run, "excluded_pages": len(excl)}


def splice_pages(md: str, replacements: dict[int, str]) -> str:
    """Replace the section after `<!-- page: N -->` (up to the next marker) for each page; everything else is kept
    byte-identical. A page with several markers gets the new body after its first marker, the others are emptied."""
    _, chunks = _chunks(md)
    known = {p for p, _, _ in chunks}
    for p in replacements:
        if p not in known:
            raise KeyError(p)
    out, last, seen = [], 0, set()
    for page, a, b in chunks:
        if page not in replacements:
            continue
        out.append(md[last:a])
        if page in seen:
            out.append("\n\n")
        else:
            body = replacements[page].strip("\n")
            out.append("\n\n" + body + "\n\n" if body else "\n\n")
            seen.add(page)
        last = b
    out.append(md[last:])
    return "".join(out)


# ---- page boundaries of Markdown without markers (spec 2026-10-05 verify §2.2)

INFER_WIN = 24
INFER_PROBES = 3
_SNAP_RADIUS = 200
_NO_TEXT = re.compile(r"<!--.*?-->|!\[[^\]]*\]\([^)]*\)", re.DOTALL)


def _monotone_keep(items: list[tuple[int, int]]) -> set[int]:
    """Pages of the longest subsequence of (page, position) — sorted by page — whose positions strictly increase.
    Ties go to the subsequence whose pages sit closest to their own rank (a page swapped away is dropped, the pages
    between the swapped ones are kept). O(n^2) over anchored pages."""
    n = len(items)
    if not n:
        return set()
    by_pos = sorted(range(n), key=lambda i: items[i][1])
    disp = [0] * n
    for rank, i in enumerate(by_pos):
        disp[i] = abs(rank - i)
    best: list[tuple[int, int]] = [(1, -disp[i]) for i in range(n)]
    prev = [-1] * n
    for i in range(n):
        pi = items[i][1]
        for j in range(i):
            if items[j][1] < pi:
                cand = (best[j][0] + 1, best[j][1] - disp[i])
                if cand > best[i]:
                    best[i], prev[i] = cand, j
    i = max(range(n), key=lambda x: best[x])
    keep: set[int] = set()
    while i != -1:
        keep.add(items[i][0])
        i = prev[i]
    return keep


def _snap(md: str, at: int, lo: int, hi: int) -> int:
    """The paragraph start (after a blank line) nearest to `at` within ±_SNAP_RADIUS and [lo, hi]; else the nearest
    line start; else `at` itself."""
    at = min(max(at, lo), hi)
    a, b = max(lo, at - _SNAP_RADIUS), min(hi, at + _SNAP_RADIUS)
    for sep in ("\n\n", "\n"):
        best = None
        i = md.find(sep, max(0, a - len(sep)))
        while i != -1 and i <= b:
            j = i + len(sep)
            while j < len(md) and md[j] == "\n":
                j += 1
            if a <= j <= b and (best is None or abs(j - at) < abs(best - at)):
                best = j
            i = md.find(sep, j)
        if best is not None:
            return best
    return at


def _edge_boundary(t: str, layer_a: str, layer_b: str, lo_t: int, hi_t: int, win: int) -> tuple[int, int] | None:
    """(end of a's last located text, start of b's first located text) in `t`, both inside [lo_t, hi_t]: the last
    window of page a's layer and the first window of page b's layer that occur exactly once there and nowhere else in
    the two layers. Leading/trailing windows that are not found (running headers, page numbers the engine dropped)
    are skipped. None when either edge cannot be located."""
    if hi_t <= lo_t:
        return None
    step = max(1, win // 2)

    def locate(s: str, other: str, offsets) -> int | None:
        for o in offsets:
            c = s[o:o + win]
            if len(c) < win or s.count(c) != 1 or c in other:
                continue
            j = t.find(c, lo_t, hi_t + win)
            if j != -1 and t.find(c, j + 1, hi_t + win) == -1:
                return j
        return None

    span_b = max(0, int(len(layer_b) * 0.5))
    start_b = locate(layer_b, layer_a, range(0, span_b + 1, step))
    if start_b is None:
        return None
    lo_a = max(0, int(len(layer_a) * 0.5))
    end_a = locate(layer_a, layer_b, range(len(layer_a) - win, lo_a - 1, -step))
    if end_a is None or end_a + win > start_b:
        return None
    return end_a + win, start_b


def infer_pages(md: str, layers: list[str], *, win: int = INFER_WIN, k: int = INFER_PROBES,
                min_chars: int = 120, exclude: Iterable[int] = ()) -> dict:
    """Page sections of Markdown without usable page markers (spec 2026-10-05 verify §2.2, index A26).

    A page is *decidable* when `_probe_spans` finds a probe (unique in the whole text layer), *anchored* when one of
    its probes occurs exactly once in the squashed Markdown and the page survives the monotone filter. `sections`
    are original-offset half-open ranges covering the whole Markdown; each boundary is the page's estimated start
    (probe position minus the probe's offset in the layer, median), kept between the neighbours' probes and snapped
    to a paragraph start. Pages without an anchor get an empty range at the next boundary; the decidable ones among
    them are `dropped_candidates` (their text is missing or out of order). `exclude`: pages whose text layer is not
    their text (broken fonts, as in `spot_check`) — never decidable."""
    n = len(layers)
    excl = set(exclude)
    whole = "\0".join(layers)
    # image links and HTML comments carry no page text (asset paths would squash to letters): blank them out,
    # keeping every offset
    t, pos = squash_with_map(_NO_TEXT.sub(lambda m: " " * len(m.group(0)), md))
    decidable: list[int] = []
    starts: dict[int, int] = {}                 # page -> estimated start in t
    first_hit: dict[int, int] = {}
    last_hit_end: dict[int, int] = {}
    for p in range(1, n + 1):
        layer = layers[p - 1]
        if len(layer) < min_chars or p in excl:
            continue
        spans = _probe_spans(layer, p, whole, win, k)
        if not spans:
            continue
        decidable.append(p)
        est, hits = [], []
        for off, probe in spans:
            i = t.find(probe)
            if i == -1 or t.find(probe, i + 1) != -1:
                continue
            est.append(i - off)
            hits.append(i)
        if est:
            est.sort()
            starts[p] = est[len(est) // 2]
            first_hit[p] = min(hits)
            last_hit_end[p] = max(hits) + win
    with_hits = sorted(starts)
    keep = _monotone_keep([(p, first_hit[p]) for p in with_hits])
    out_of_order = [p for p in with_hits if p not in keep]
    anchored = sorted(keep)
    undecidable = sorted((set(range(1, n + 1)) - set(decidable)) | set(out_of_order))
    dropped = sorted(p for p in decidable if p not in keep)
    res = {"method": "inferred", "expected": n, "found": None, "coverage": None, "anchored": len(anchored),
           "decidable": len(decidable),
           "confidence": round(len(anchored) / len(decidable), 4) if decidable else 0.0,
           "sections": {}, "undecidable": undecidable, "dropped_candidates": dropped, "out_of_order": out_of_order}
    if not anchored:
        return res

    def orig(i: int) -> int:                    # squashed offset -> original offset of that character
        if i <= 0:
            return 0
        return pos[i] if i < len(pos) else len(md)

    bounds: dict[int, int] = {anchored[0]: 0}
    edges = 0
    for a, b in itertools.pairwise(anchored):
        lo_t, hi_t = last_hit_end[a], first_hit[b]      # page b starts after a's last probe, before b's first one
        lo = orig(lo_t - 1) + 1
        edge = _edge_boundary(t, layers[a - 1], layers[b - 1], lo_t, hi_t, win)
        if edge is not None:
            end_a, start_b = edge
            # the paragraph that holds b's first located text, unless a's last located text is in it too
            # (a paragraph that runs across the page break): then the nearest line start
            hi = orig(start_b)
            par = md.rfind("\n\n", 0, hi + 1)
            lo_edge = max(lo, orig(end_a - 1) + 1)
            if par != -1 and par + 2 >= lo_edge:
                j = par + 2
                while j < hi and md[j] == "\n":
                    j += 1
                bounds[b] = j
            else:
                bounds[b] = _snap(md, hi, lo_edge, hi)
            edges += 1
            continue
        # no located page-edge text: midpoint of b's start estimate (early by b's dropped running header) and a's
        # end estimate (start + layer length; late by a's dropped footer)
        at_t = (starts[a] + len(layers[a - 1]) + starts[b]) // 2
        at_t = min(max(at_t, lo_t), hi_t)
        bounds[b] = _snap(md, orig(at_t), lo, orig(hi_t))
    res["edge_located"] = edges
    sections: dict[int, tuple[int, int]] = {}
    seq = sorted(bounds.items())
    for idx, (p, s0) in enumerate(seq):
        sections[p] = (s0, seq[idx + 1][1] if idx + 1 < len(seq) else len(md))
    for p in range(1, n + 1):
        if p not in sections:
            at = next((bounds[q] for q in anchored if q > p), len(md))
            sections[p] = (at, at)
    res["sections"] = dict(sorted(sections.items()))
    return res


def apply_inferred(md: str, sections: dict[int, tuple[int, int]]) -> str:
    """`md` with `<!-- page: N -->` before each inferred section (an empty section keeps its marker), so
    `split_pages` and the per-page checks work unchanged."""
    if not sections:
        return md
    order = sorted(sections.items(), key=lambda kv: (kv[1][0], kv[0]))
    out = [md[:order[0][1][0]]]
    for p, (a, b) in order:
        body = md[a:b]
        out.append(f"<!-- page: {p} -->\n\n{body}")
        if not body.endswith("\n"):
            out.append("\n\n")
    return "".join(out)
