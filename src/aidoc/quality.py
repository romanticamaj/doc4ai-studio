"""Quality assessment (spec §4 品質檢查; per-page model: spec 2026-10-01 §5). Thresholds are initial; tune on
fixtures."""
from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path

from aidoc import pagemap
from aidoc.models import ProbeResult, QualityResult

MIN_CHARS_PER_PAGE = 50
MAX_GARBAGE_RATIO = 0.05

# spec 2026-10-01 §5 (numbers from the spike, §3)
COVERAGE_MIN = 0.95
ALIGN_MIN = 0.90
ALIGN_MIN_DECIDABLE = 5
# a single misplaced page is local (repeated content, a merged cross-page paragraph), not a shifted page map: a
# shift misplaces nearly every decidable page (2026-10-01 acceptance, Head First Android pages 81-120: 6/7)
ALIGN_MIN_MISPLACED = 2
# per-segment fail-fast check: only a grossly wrong map (a shifted map scores <= 0.1). Correct segments of a book whose
# exercise / solution pages repeat each other's text score 0.80-0.89 (Head First Android, Docling); the document
# check over 30 pages of the whole book keeps ALIGN_MIN (2026-10-01 acceptance)
ALIGN_MIN_QUICK = 0.50
# a local shift misplaces consecutive pages; legitimate misses are isolated (longest run over every page of the two
# real books: 2 and 1). Applies to the segment and the document check (2026-10-01 verifier findings)
ALIGN_MAX_RUN = 3
FLAGGED_MAX_RATIO = 0.20
PAGE_GARBAGE_MAX = 0.05
PAGE_GARBAGE_MIN_CHARS = 50
SHIFT_HITS_MIN = 3
# 2: page_dropped joins the per-page reasons (spec 2026-10-05 verify §2.3): existing documents are re-graded once
# 3: page_partial / page_misaligned / page_oversized, history carried over, no stamp without a PDF
# 4: verifier round 2 - dropped on the body layer, n-gram coverage, text found elsewhere (tags stripped), hosts need
#    two probes, furniture judged per document, link URLs stripped, repairs re-validated
# 5: verifier round 3 - page_partial advisory, broken fonts per page by share (no symbol fonts), wrong-Unicode-map
#    guard, figure lines out of the prose layer
PAGE_CHECK_VERSION = 5
DROPPED_LAYER_MIN = 120        # squashed prose-layer chars a page needs before an empty section means "dropped" ...
DROPPED_BODY_MIN = 300         # ... or body-layer chars (full-page tables, text-heavy diagrams: short lines only)
DROPPED_SECTION_MAX = 20       # squashed chars (markup stripped) below which a section counts as empty
DROPPED_COVERAGE_MAX = 0.2     # a section holding less of the body layer than this ...
DROPPED_TEXT_MAX = 150         # ... in fewer squashed chars than this (a heading, a caption) is page_dropped too
COVER_N = 5                    # coverage n-gram (characters of the squashed text)
PARTIAL_COVERAGE_MIN = 0.5     # a section holding less of its page's prose layer is page_partial; a repair of a
                               # dropped / partial page must reach it to count as repaired
ELSEWHERE_MIN = 0.5            # another section holding this much of a page's body (beyond its own layer) hosts it
SPLIT_PRESENT_MIN = 0.8        # own + elsewhere this high (own < PARTIAL_COVERAGE_MIN): the text is all there, split
MOVED_MIN = 0.3                # an (almost) empty section (< DROPPED_COVERAGE_MAX) whose page has this much of its body
                               # in another section: the text moved there; an OCR copy would duplicate it
NEIGHBOUR_PAGES = 4            # n-gram "elsewhere" search radius; the probes (page_homes) search the whole Markdown
DISTINCT_PAGES = 8             # "elsewhere" counts only a page's n-grams found on no other page within this radius ...
DISTINCT_MIN = 40              # ... and decides nothing with fewer of them
GIB_GAP = 1.5                  # bits per bigram a section may read better than its layer before the layer is untrusted
GIB_LAYER_MIN = 120            # layer / section characters needed to compare them
GIB_SECTION_MIN = 60
GIB_VOCAB = 1600               # add-0.5 smoothing vocabulary of the bigram model
MISALIGNED_MIN_PROBES = 2      # a page is page_misaligned when at least this many of its probes sit elsewhere
PAGE_REASONS = ("page_dropped", "page_misaligned", "page_oversized")   # level-raising per-page check reasons
# page_partial is an advisory (verifier round 3: blind relabel kappa 0.29): shown per page, never raises the level,
# never triggers repair or engine fallback, until it passes on a fresh held-out set
PAGE_ADVISORIES = ("page_partial",)
VLM_OVERLAP_MIN = 0.2          # VLM spec §7: advisory only (collect numbers; never changes level or score)
VLM_OVERLAP_MIN_LAYER = 120    # squashed text-layer chars a page needs before the overlap means anything
SHIFT = 29                     # TrueType standard Mac glyph order: glyph id = char code - 29 (glyph 3 = space)
COMMON_WORDS = frozenset(
    ["the", "and", "you", "that", "it", "is", "to", "of", "in", "for", "on", "with", "this", "be", "are", "your", "can", "if", "what", "there", "but", "was", "as", "at", "by", "from", "or", "an", "not", "have", "will", "all", "so", "do", "just", "like", "how", "app"])

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_SEP_LINE = re.compile(r"(?m)^\s*\|?\s*:?-{3,}[\s|:-]*$")
_MARKUP_CHARS = re.compile(r"[|#*_`$]")
_TABLE_GFM = re.compile(r"(?m)^\s*\|.*\|\s*$\n\s*\|?\s*:?-{3,}")
_TABLE_HTML = re.compile(r"<table", re.IGNORECASE)
# Mojibake (UTF-8 bytes decoded as cp1252/latin-1): runs over this alphabet that contain at least one Latin-1
# symbol/control. Letters-only runs ("ÄÖÜß") and common symbols (★ → ● … ━ ①) are never garbage.
_MOJIBAKE_ALPHABET = (set(map(chr, range(0x80, 0x180)))
                      | set("\u0192\u02c6\u02dc\u2013\u2014\u2018\u2019\u201a\u201c\u201d\u201e\u2020\u2021"
                            "\u2022\u2026\u2030\u2039\u203a\u20ac\u2122"))
_MOJIBAKE_MIN_RUN = 4


def strip_markup(markdown: str) -> str:
    s = _COMMENT.sub("", markdown)
    s = _IMAGE.sub("", s)
    s = _SEP_LINE.sub("", s)
    return _MARKUP_CHARS.sub("", s)


def has_table(markdown: str) -> bool:
    return bool(_TABLE_GFM.search(markdown) or _TABLE_HTML.search(markdown))


def _is_garbage_char(c: str) -> bool:
    """U+FFFD, private use, C0/C1 controls (except whitespace) and unassigned code points."""
    if c in "\n\t\r":
        return False
    return c == "\ufffd" or unicodedata.category(c) in ("Co", "Cn", "Cs", "Cc")


def _mojibake_spans(s: str):
    i, n = 0, len(s)
    while i < n:
        if s[i] not in _MOJIBAKE_ALPHABET:
            i += 1
            continue
        j = i
        while j < n and s[j] in _MOJIBAKE_ALPHABET:
            j += 1
        run = s[i:j]
        latin1 = [c for c in run if 0x80 <= ord(c) <= 0xFF]
        if len(run) >= _MOJIBAKE_MIN_RUN and any(ord(c) >= 0xA0 for c in latin1) \
                and any(unicodedata.category(c)[0] in "SPC" for c in latin1):
            yield i, j
        i = j


def garbage_ratio(markdown: str) -> float:
    s = _COMMENT.sub("", markdown)
    non_space = [c for c in s if not c.isspace()]
    if not non_space:
        return 0.0
    flagged = [_is_garbage_char(c) for c in s]
    for a, b in _mojibake_spans(s):
        for i in range(a, b):
            if not s[i].isspace():
                flagged[i] = True
    return sum(flagged) / len(non_space)


def _assess_base(markdown: str, probe: ProbeResult, pages: int | None) -> QualityResult:
    text = strip_markup(markdown)
    chars = sum(1 for c in text if not c.isspace())
    reasons: list[str] = []
    if pages is not None:
        effective = max(1, pages - len(probe.blank_pages))
        cpp = chars / effective
    else:
        effective = None
        cpp = float(chars)
    if cpp < MIN_CHARS_PER_PAGE:
        reasons.append("chars_per_page")
    ratio = garbage_ratio(markdown)
    if ratio > MAX_GARBAGE_RATIO:
        reasons.append("garbage_ratio")
    table = has_table(markdown)
    missing = probe.has_table_lines and not table
    if missing:
        reasons.append("missing_table")
    score = (0.5 * min(1.0, cpp / 200) + 0.3 * max(0.0, 1 - ratio / MAX_GARBAGE_RATIO)
             + 0.2 * (0 if missing else 1))
    return QualityResult(score=round(score, 3), level="ok" if not reasons else "low", reasons=reasons,
                         metrics={"chars": chars, "chars_per_page": round(cpp, 2),
                                  "garbage_ratio": round(ratio, 4), "effective_pages": effective,
                                  "has_table": table})


# ---- per-page signals (spec 2026-10-01 §5.1, §8.1)

_FFFD_BETWEEN = re.compile(r"(?<=\S)\N{REPLACEMENT CHARACTER}(?=\S)")
_ASCII_RUN = re.compile(r"[\x21-\x7e]+")


def _shift_hits(text: str) -> int:
    hits = 0
    for m in _ASCII_RUN.finditer(text):                # U+FFFD and any non-ASCII char separate tokens
        tok = m.group(0)
        if len(tok) > 12:
            continue
        codes = [ord(c) + SHIFT for c in tok]
        if max(codes) > 0x7E:
            continue
        if "".join(map(chr, codes)).lower() in COMMON_WORDS:
            hits += 1
    return hits


def page_signals(text: str) -> dict:
    stripped = strip_markup(text)
    return {"chars": sum(1 for c in stripped if not c.isspace()),
            "fffd_between": len(_FFFD_BETWEEN.findall(text)),
            "garbage": round(garbage_ratio(text), 4),
            "shift_hits": _shift_hits(_COMMENT.sub("", text))}


def flag_page(sig: dict, broken_font: bool) -> list[str]:
    garbage = sig["chars"] >= PAGE_GARBAGE_MIN_CHARS and sig["garbage"] > PAGE_GARBAGE_MAX
    if broken_font and (sig["fffd_between"] >= 1 or garbage or sig["shift_hits"] >= SHIFT_HITS_MIN):
        return ["broken_text_layer"]
    if garbage:
        return ["garbage"]
    return []


def flag_pages(md: str, broken_font_pages: Iterable[int]) -> list[dict]:
    """Flagged pages of a marked-up document: [{page, reasons, metrics}] sorted by page."""
    _, secs = pagemap.split_pages(md)
    broken = set(broken_font_pages)
    out = []
    for page in sorted(secs):
        sig = page_signals(secs[page])
        reasons = flag_page(sig, page in broken)
        if reasons:
            out.append({"page": page, "reasons": reasons,
                        "metrics": {"fffd_between": sig["fffd_between"], "garbage": sig["garbage"],
                                    "shift_hits": sig["shift_hits"]}})
    return out


def _grams(s: str, n: int = COVER_N) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in range(len(s) - n + 1):
        g = s[i:i + n]
        out[g] = out.get(g, 0) + 1
    return out


def section_text(section: str) -> str:
    """A page section as comparable text: comments, images, markup, HTML tags and link URLs removed, squashed."""
    return pagemap.squash(pagemap.strip_tags(strip_markup(section)))


def _cover(sec: dict[str, int], layer: dict[str, int]) -> float:
    total = sum(layer.values())
    return round(sum(min(c, sec.get(g, 0)) for g, c in layer.items()) / total, 4) if total else 0.0


def coverage(section: str, layer: str) -> float | None:
    """Share of the page's (squashed) text layer that the section holds: multiset coverage of character COVER_N-grams,
    markup, HTML tags and link URLs removed. None for a layer shorter than one n-gram. (Bigrams were too weak: any
    English text covers most of another page's bigrams — verifier round 2.)"""
    if len(layer) < COVER_N:
        return None
    return _cover(_grams(section_text(section)), _grams(layer))


def _excess_cover(sec: dict[str, int], own: dict[str, int], layer: dict[str, int]) -> float:
    """Share of `layer` (another page's body) that `sec` holds beyond what its own page's text layer `own` accounts
    for: a solution page repeating its exercise holds no foreign text, a section with a neighbour's table does."""
    total = sum(layer.values())
    if not total:
        return 0.0
    hit = sum(min(c, max(0, sec.get(g, 0) - own.get(g, 0))) for g, c in layer.items())
    return round(hit / total, 4)


def _bigram_counts(t: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in range(len(t) - 1):
        g = t[i:i + 2]
        out[g] = out.get(g, 0) + 1
    return out


def _lm_score(t: str, lm: dict[str, int], n: int, ex: dict[str, int], n_ex: int) -> float:
    """Mean log2 probability of t's character bigrams under the document's bigram model minus `ex` (leave-one-out:
    a section must not be scored by a model trained on itself)."""
    v = GIB_VOCAB
    return sum(math.log2((lm.get(t[i:i + 2], 0) - ex.get(t[i:i + 2], 0) + 0.5) / (n - n_ex + 0.5 * v))
               for i in range(len(t) - 1)) / (len(t) - 1)


def language_gap(md: str, layers: list[str]) -> dict[int, float]:
    """{page: how many bits per character bigram the page's own section reads more like the document than its text
    layer does} for pages with GIB_LAYER_MIN layer and GIB_SECTION_MIN section characters. The model is the
    document's own Markdown (character bigrams, leave-one-out), so it works for any script."""
    _, secs = pagemap.split_pages(md)
    texts = {p: section_text(t) for p, t in secs.items()}
    lm: dict[str, int] = {}
    for t in texts.values():
        for g, c in _bigram_counts(t).items():
            lm[g] = lm.get(g, 0) + c
    n = sum(lm.values())
    out: dict[int, float] = {}
    for p, lay in enumerate(layers, 1):
        st = texts.get(p, "")
        if len(lay) < GIB_LAYER_MIN or len(st) < GIB_SECTION_MIN:
            continue
        ex = _bigram_counts(st)
        n_ex = sum(ex.values())
        out[p] = round(_lm_score(st, lm, n, ex, n_ex) - _lm_score(lay, lm, n, ex, n_ex), 3)
    return out


def untrusted_layers(md: str, layers: list[str]) -> set[int]:
    """Pages whose text layer looks like a shifted cipher or gibberish next to their own section (a wrong Unicode
    map: verifier round 3, 34 false page_partial flags on the synthetic broken-font samples): GIB_GAP bits or more."""
    return {p for p, g in language_gap(md, layers).items() if g >= GIB_GAP}


def page_checks(md: str, layers: list[str], content: list[str], blank_pages: Iterable[int] = (),
                broken_font_pages: Iterable[int] = (), *, body: list[str] | None = None) -> list[dict]:
    """Per-page structural checks against the PDF text layer (spec 2026-10-05 verify §2.3, verifier rounds 1-2).
    `layers` are the squashed text layers, `content` the prose layers, `body` the furniture-free layers
    (`pagemap.pdf_layers`; without `body` the full layers stand in).

    - page_misaligned: most of the page's text sits in other sections — its own section covers less than
      PARTIAL_COVERAGE_MIN of its body and another section within NEIGHBOUR_PAGES holds at least ELSEWHERE_MIN of it
      beyond that section's own text (or the two together hold SPLIT_PRESENT_MIN: split, all there; or the section
      is almost empty and the other holds MOVED_MIN) — "elsewhere" counts the page's distinctive n-grams only (on no
      other page within DISTINCT_PAGES) — or at least
      MISALIGNED_MIN_PROBES of its unique probes were found and none in its own section. Never OCR-repaired: that
      would duplicate the text.
    - page_dropped: the prose layer has DROPPED_LAYER_MIN chars or the body layer DROPPED_BODY_MIN, and the section
      is empty (DROPPED_SECTION_MAX) or holds a heading / caption only (covers < DROPPED_COVERAGE_MAX of the body in
      < DROPPED_TEXT_MAX chars).
    - page_partial: the section covers less than PARTIAL_COVERAGE_MIN of the prose layer.
    - page_oversized: the section holds another page's text: ELSEWHERE_MIN of that page's distinctive n-grams beyond
      the section's own text layer (MOVED_MIN for a page that moved there) — also added to a dropped / partial page,
      which is then not repaired. Neither a probe count nor the section's size decides it: on held-out pages, a
      paragraph continued across the page break put two probes of the next page in the section, and the OCR of a
      screenshot made a section three times its layer (verifier round 2).
    Tags and link URLs are stripped everywhere. Untrusted pages (`broken_font_pages`: their layer is not their text)
    only get page_dropped on an empty section."""
    _, secs = pagemap.split_pages(md)
    n = len(layers)
    body = body if body is not None and len(body) == n else layers
    blank, broken = set(blank_pages), set(broken_font_pages)
    broken |= untrusted_layers(md, layers)        # wrong Unicode map: the layer is not the page's text
    texts = {p: section_text(s) for p, s in secs.items()}
    sec_g = {p: _grams(t) for p, t in texts.items()}
    own_g: dict[int, dict[str, int]] = {}

    def own(p: int) -> dict[str, int]:
        if p not in own_g:
            own_g[p] = _grams(layers[p - 1]) if 1 <= p <= n else {}
        return own_g[p]

    own_s: dict[int, set[str]] = {}

    def own_set(p: int) -> set[str]:
        if p not in own_s:
            own_s[p] = set(own(p))
        return own_s[p]

    entries: dict[int, dict] = {}
    foreign: dict[int, set[int]] = {}
    body_g: dict[int, dict[str, int]] = {}
    for q in range(1, n + 1):
        if q in broken or len(body[q - 1]) < DROPPED_LAYER_MIN:
            continue
        bg = body_g[q] = _grams(body[q - 1])
        mine = _cover(sec_g.get(q, {}), bg)
        # only the page's own wording says where it went: n-grams also on a nearby page (an exercise and its
        # solution, a code listing shown twice) cannot (verifier round 2: Head First pp. 141-144)
        near = [own_set(p) for p in range(max(1, q - DISTINCT_PAGES), min(n, q + DISTINCT_PAGES) + 1) if p != q]
        dist = {g: c for g, c in bg.items() if not any(g in st for st in near)}
        if sum(dist.values()) < DISTINCT_MIN:
            continue
        shares = {p: _excess_cover(sec_g[p], own(p), dist)
                  for p in range(max(1, q - NEIGHBOUR_PAGES), q + NEIGHBOUR_PAGES + 1) if p != q and p in sec_g}
        best = max(shares.values(), default=0.0)
        moved = mine < PARTIAL_COVERAGE_MIN and (
            best >= ELSEWHERE_MIN or (best > 0 and mine + best >= SPLIT_PRESENT_MIN)
            or (mine < DROPPED_COVERAGE_MAX and best >= MOVED_MIN))
        hosts = [p for p, sh in shares.items() if sh >= (min(MOVED_MIN, best / 2) if moved else ELSEWHERE_MIN)]
        for p in hosts:
            foreign.setdefault(p, set()).add(q)
        if moved:
            entries[q] = {"page": q, "reasons": ["page_misaligned"],
                          "metrics": {"found_in": sorted(hosts), "coverage": mine, "elsewhere": best}}
    homes = pagemap.page_homes(md, layers, exclude=broken)
    for q, h in homes.items():
        mine = _cover(sec_g.get(q, {}), body_g[q]) if q in body_g else 0.0
        if q not in entries and h["found"] >= MISALIGNED_MIN_PROBES and not h["home"] and mine < PARTIAL_COVERAGE_MIN:
            entries[q] = {"page": q, "reasons": ["page_misaligned"],
                          "metrics": {"found_in": sorted(h["hosts"]), "probes_found": h["found"], "coverage": mine}}
    for p in range(1, n + 1):
        if p in entries or p not in secs:
            continue
        layer, prose, bl = layers[p - 1], content[p - 1] if p - 1 < len(content) else layers[p - 1], body[p - 1]
        chars = len(texts[p])
        hosts = sorted(foreign.get(p, ()))
        reasons: list[str] = []
        advisories: list[str] = []
        metrics: dict = {"layer_chars": len(layer), "section_chars": chars}
        if p not in blank:
            textful = len(prose) >= DROPPED_LAYER_MIN or len(bl) >= DROPPED_BODY_MIN
            cov_body = _cover(sec_g[p], body_g[p] if p in body_g else _grams(bl)) if textful else None
            if textful and (chars < DROPPED_SECTION_MAX or (
                    p not in broken and cov_body is not None and cov_body < DROPPED_COVERAGE_MAX
                    and chars < DROPPED_TEXT_MAX)):
                reasons.append("page_dropped")
                metrics.update(body_chars=len(bl), coverage=cov_body)
            elif p not in broken and len(prose) >= DROPPED_LAYER_MIN:
                cov = coverage(secs[p], prose)
                if cov is not None and cov < PARTIAL_COVERAGE_MIN:
                    advisories.append("page_partial")       # advisory (verifier round 3): shown, never level-raising
                    metrics.update(coverage=cov)
        if hosts:
            reasons.append("page_oversized")
            metrics["foreign_pages"] = hosts
        if reasons or advisories:
            entries[p] = {"page": p, "reasons": reasons, "metrics": metrics}
            if advisories:
                entries[p]["advisories"] = advisories
    return [entries[p] for p in sorted(entries)]


def dropped_pages(md: str, layers: list[str], blank_pages: Iterable[int] = ()) -> list[dict]:
    """page_dropped only (kept for callers that have no content layers): see `page_checks`."""
    return [e for e in page_checks(md, layers, layers, blank_pages) if "page_dropped" in e["reasons"]]


def vlm_overlap(section: str, layer: str) -> float | None:
    """Character 3-gram Jaccard between a VLM page section and the page's (squashed) PDF text layer — a coarse
    hallucination signal (VLM spec §7). None when the layer is too short to say anything."""
    if len(layer) < VLM_OVERLAP_MIN_LAYER:
        return None
    a = pagemap.squash(strip_markup(section))
    ga = {a[i:i + 3] for i in range(len(a) - 2)}
    gb = {layer[i:i + 3] for i in range(len(layer) - 2)}
    if not ga or not gb:
        return 0.0
    return round(len(ga & gb) / len(ga | gb), 3)


def _vlm_advisories(markdown: str, entries: list[dict], vlm_pages, layers, broken: set[int]) -> list[dict]:
    """Add `advisories: ["vlm_low_overlap"]` + `metrics.vlm_overlap` for VLM-made pages whose text barely overlaps
    the PDF text layer. Advisory-only entries carry no `reasons` and are never flagged or unrepaired pages."""
    _, secs = pagemap.split_pages(markdown)
    by_page = {e["page"]: e for e in entries}
    for p in sorted(set(vlm_pages)):
        if p in broken or not 1 <= p <= len(layers):
            continue
        ov = vlm_overlap(secs.get(p, ""), layers[p - 1])
        if ov is None or ov >= VLM_OVERLAP_MIN:
            continue
        e = by_page.get(p)
        if e is None:
            e = by_page[p] = {"page": p, "reasons": []}
        e["advisories"] = sorted(set(e.get("advisories") or []) | {"vlm_low_overlap"})
        e["metrics"] = {**(e.get("metrics") or {}), "vlm_overlap": ov}
    return [by_page[p] for p in sorted(by_page)]


def assess(markdown: str, probe: ProbeResult, pages: int | None, *, pdf: Path | None = None,
           page_map_method: str | None = None, quick: bool = False, flagged_before: int | None = None,
           repaired: list[dict] | None = None, align_sample: int | None = None, last_resort: bool = False,
           failed_pages: dict[int, str] | None = None,
           layers: list[str] | None = None, vlm_pages=None,
           skip_page_map_reasons: bool = False, content_layers: list[str] | None = None,
           body_layers: list[str] | None = None, untrusted_pages: list[int] | None = None) -> QualityResult:
    """Document quality. Legacy rules for non-PDFs; PDFs add the page map, alignment spot check and flagged pages
    (spec 2026-10-01 §5.2: any document reason -> low; else unrepaired flagged pages -> warn; else ok).
    `skip_page_map_reasons`: the markers were inferred by `verify` (index A26) — the page map is not evaluated
    (no coverage / invalid / alignment reasons, no spot check)."""
    base = _assess_base(markdown, probe, pages)
    base.page_check = PAGE_CHECK_VERSION
    if probe.kind != "pdf" or pages is None:
        return base
    reasons = list(base.reasons)
    pm = pagemap.page_map(markdown, pages, page_map_method)
    if pm["coverage"] < COVERAGE_MIN and not skip_page_map_reasons:
        reasons.append("page_map_incomplete")
    # runners emit one marker per page in order: a duplicate, a decrease or a page outside 1..pages means the map
    # was relabelled somewhere (a shifted / swapped / repeated range) — deterministic, no sampling
    pm["invalid_markers"] = pagemap.invalid_markers(markdown, pages)
    if pm["invalid_markers"] and not skip_page_map_reasons:
        reasons.append("page_map_invalid")
    if pdf is not None and (layers is None or (content_layers is None and not quick))             and (vlm_pages or not quick) and Path(pdf).is_file():
        layers, content_layers, body_layers, untrusted_pages = pagemap.pdf_layers(pdf)   # once, for every check
    if pdf is not None and not skip_page_map_reasons:
        # quick (segment) check: every page of the segment — a 10-page sample let one legitimately merged
        # cross-page paragraph read as 1/9 misaligned on a real book (2026-10-01 acceptance)
        al = pagemap.spot_check(markdown, pdf, sample=align_sample or (max(pages, 1) if quick else 30),
                                exclude=probe.broken_font_pages, layers=layers)
        pm["alignment"] = al
        low_ratio = (al["decidable"] >= ALIGN_MIN_DECIDABLE and al["ratio"] is not None
                     and al["ratio"] < (ALIGN_MIN_QUICK if quick else ALIGN_MIN)
                     and al["decidable"] - al["aligned"] >= ALIGN_MIN_MISPLACED)
        if low_ratio or al.get("max_misplaced_run", 0) >= ALIGN_MAX_RUN:
            reasons.append("page_map_misaligned")
    repaired = list(repaired or [])
    # every page, repaired ones too, goes through the page checks; a repair (from this conversion or, on a re-grade,
    # from the history) whose section still fails them no longer counts as repaired (verifier round 2)
    untrusted = probe.broken_font_pages if untrusted_pages is None else untrusted_pages
    checks = ({e["page"]: e for e in page_checks(markdown, layers, content_layers or layers, probe.blank_pages,
                                                 untrusted, body=body_layers)}
              if not quick and layers is not None else {})
    kept = []
    for e in repaired:
        c = checks.get(e["page"])
        if c is not None and set(c["reasons"]) & set(PAGE_REASONS):
            kept_out = {**c, "repair_rejected": e.get("repaired_by")}
            checks[e["page"]] = kept_out
        else:
            kept.append(e)
    repaired = kept
    repaired_pages = {e["page"] for e in repaired}
    suspect = sorted(set(probe.broken_font_pages) | set(probe.font_suspect_pages))   # garbage check: any broken font
    flagged = [e for e in flag_pages(markdown, suspect) if e["page"] not in repaired_pages]
    # pages the engine reported as failed and that were not repaired (their section is empty or partial)
    by_page = {e["page"]: e for e in flagged}
    for p, msg in sorted((failed_pages or {}).items()):
        if p in repaired_pages or not 1 <= p <= pages:
            continue
        if p in by_page:
            by_page[p] = {**by_page[p], "reasons": by_page[p]["reasons"] + ["page_conversion_failed"], "error": msg}
        else:
            by_page[p] = {"page": p, "reasons": ["page_conversion_failed"], "error": msg}
    # pages lost, cut, moved or swollen without the engine saying so; not in the quick segment check
    advisory: list[dict] = []                   # advisory-only pages (page_partial): shown, never counted
    for p, e in checks.items():
        if p in repaired_pages or p > pages:
            continue
        if not e["reasons"]:
            advisory.append(e)
            continue
        if p in by_page:
            if "repair_rejected" in e:
                by_page[p] = {**by_page[p], "reasons": by_page[p]["reasons"] + e["reasons"],
                              "repair_rejected": e["repair_rejected"]}
        else:
            by_page[p] = e
    flagged = [by_page[p] for p in sorted(by_page)]
    missing = [{"page": p, "reasons": ["page_map_missing"]} for p in pm["missing"]]
    entries = sorted(flagged + repaired + missing + advisory, key=lambda e: e["page"])
    n_flagged = flagged_before if flagged_before is not None else len(flagged) + len(repaired)
    effective = max(1, pages - len(probe.blank_pages))
    # pages_flagged means "this engine is wrong for the file: use another one". With no engine left (forced engine or
    # the last fallback) every flagged page was sent to repair instead, and only pages still broken count.
    still_broken = sum(1 for e in flagged if not e.get("repaired_by"))
    if not quick and (still_broken if last_resort else n_flagged) > FLAGGED_MAX_RATIO * effective:
        reasons.append("pages_flagged")
    unrepaired = sum(1 for e in entries if e.get("reasons") and not e.get("repaired_by"))
    # missing pages beyond the listed 200 still count as unrepaired
    unrepaired += max(0, (pm["expected"] - pm["found"]) - len(pm["missing"]))
    level = "low" if reasons else ("warn" if unrepaired else "ok")
    score = base.score * pm["coverage"] * max(0.0, 1 - unrepaired / effective)
    if vlm_pages and layers:
        entries = _vlm_advisories(markdown, entries, vlm_pages, layers, set(probe.broken_font_pages))
    return QualityResult(score=round(score, 3), level=level, reasons=reasons, metrics=base.metrics,
                         page_check=PAGE_CHECK_VERSION, page_map=pm, pages=entries, pages_flagged=n_flagged)
