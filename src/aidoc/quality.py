"""Quality assessment (spec §4 品質檢查; per-page model: spec 2026-10-01 §5). Thresholds are initial; tune on
fixtures."""
from __future__ import annotations

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
PAGE_CHECK_VERSION = 3
DROPPED_LAYER_MIN = 120        # squashed text-layer chars a page needs before an empty section means "dropped"
DROPPED_SECTION_MAX = 20       # non-space chars (markup stripped) below which a section counts as empty
PARTIAL_COVERAGE_MIN = 0.5     # a section holding less of its page's content layer is page_partial; a repair of a
                               # dropped / partial page must reach it to count as repaired
OVERSIZE_RATIO = 2.0           # a section this many times its page's text layer ...
OVERSIZE_SLACK = 300           # ... plus this many characters is page_oversized
MISALIGNED_MIN_PROBES = 2      # a page is page_misaligned when at least this many of its probes sit elsewhere
PAGE_REASONS = ("page_dropped", "page_partial", "page_misaligned", "page_oversized")
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
_TAG = re.compile(r"</?[A-Za-z][^>]*>")
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


def _bigrams(s: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in range(len(s) - 1):
        g = s[i:i + 2]
        out[g] = out.get(g, 0) + 1
    return out


def coverage(section: str, layer: str) -> float | None:
    """Share of the page's (squashed) text layer that the section holds: multiset coverage of character bigrams,
    markup and HTML tags removed. None for an empty layer."""
    if len(layer) < 2:
        return None
    sec = pagemap.squash(strip_markup(_TAG.sub(" ", section)))
    a, b = _bigrams(layer), _bigrams(sec)
    hit = sum(min(c, b.get(g, 0)) for g, c in a.items())
    return round(hit / max(1, len(layer) - 1), 4)


def page_checks(md: str, layers: list[str], content: list[str], blank_pages: Iterable[int] = (),
                broken_font_pages: Iterable[int] = ()) -> list[dict]:
    """Per-page structural checks against the PDF text layer (verifier round on spec 2026-10-05 verify §2.3):

    - page_misaligned: every probe of the page that was found sits in another page's section (swapped content,
      a page's text under a neighbour) — never treated as dropped, so it is never OCR-copied a second time;
    - page_dropped: the content layer (running header / footer / page number removed) has DROPPED_LAYER_MIN chars,
      the section fewer than DROPPED_SECTION_MAX;
    - page_partial: the section covers less than PARTIAL_COVERAGE_MIN of the content layer;
    - page_oversized: the section holds another page's text (that page's probes, misplaced or duplicated) or is
      far larger than its own text layer.
    Broken-font pages (their layer is not their text) only get page_dropped."""
    _, secs = pagemap.split_pages(md)
    blank, broken = set(blank_pages), set(broken_font_pages)
    homes = pagemap.page_homes(md, layers, exclude=broken)
    entries: dict[int, dict] = {}
    foreign: dict[int, set[int]] = {}
    for q, h in homes.items():
        if h["found"] >= MISALIGNED_MIN_PROBES and not h["home"]:
            entries[q] = {"page": q, "reasons": ["page_misaligned"],
                          "metrics": {"found_in": sorted(h["hosts"]), "probes_found": h["found"]}}
            for p in h["hosts"]:
                foreign.setdefault(p, set()).add(q)
        for p in h["duplicated_in"]:
            foreign.setdefault(p, set()).add(q)
    for p, layer in enumerate(layers, 1):
        if p in entries or p in blank or p not in secs:
            continue
        cl = content[p - 1] if p - 1 < len(content) else layer
        sec = secs[p]
        chars = sum(1 for c in strip_markup(sec) if not c.isspace())
        if len(cl) >= DROPPED_LAYER_MIN and chars < DROPPED_SECTION_MAX:
            entries[p] = {"page": p, "reasons": ["page_dropped"],
                          "metrics": {"layer_chars": len(cl), "section_chars": chars}}
            continue
        if p in broken:
            continue
        if len(cl) >= DROPPED_LAYER_MIN:
            cov = coverage(sec, cl)
            if cov is not None and cov < PARTIAL_COVERAGE_MIN:
                entries[p] = {"page": p, "reasons": ["page_partial"],
                              "metrics": {"coverage": cov, "layer_chars": len(cl), "section_chars": chars}}
                continue
        size = len(pagemap.squash(strip_markup(_TAG.sub(" ", sec))))
        too_big = len(layer) >= DROPPED_SECTION_MAX and size > OVERSIZE_RATIO * len(layer) + OVERSIZE_SLACK
        if p in foreign or too_big:
            entries[p] = {"page": p, "reasons": ["page_oversized"],
                          "metrics": {"foreign_pages": sorted(foreign.get(p, ())), "section_chars": size,
                                      "layer_chars": len(layer)}}
    return [entries[p] for p in sorted(entries)]


def dropped_pages(md: str, layers: list[str], blank_pages: Iterable[int] = ()) -> list[dict]:
    """page_dropped only (kept for callers that have no content layers): see `page_checks`."""
    return [e for e in page_checks(md, layers, layers, blank_pages) if e["reasons"] == ["page_dropped"]]


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
           skip_page_map_reasons: bool = False, content_layers: list[str] | None = None) -> QualityResult:
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
        layers, content_layers = pagemap.pdf_layers(pdf)   # once: spot check, page checks, VLM overlap
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
    repaired_pages = {e["page"] for e in repaired}
    flagged = [e for e in flag_pages(markdown, probe.broken_font_pages) if e["page"] not in repaired_pages]
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
    if not quick and layers is not None:
        for e in page_checks(markdown, layers, content_layers or layers, probe.blank_pages, probe.broken_font_pages):
            if e["page"] not in repaired_pages and e["page"] not in by_page and e["page"] <= pages:
                by_page[e["page"]] = e
    flagged = [by_page[p] for p in sorted(by_page)]
    missing = [{"page": p, "reasons": ["page_map_missing"]} for p in pm["missing"]]
    entries = sorted(flagged + repaired + missing, key=lambda e: e["page"])
    n_flagged = flagged_before if flagged_before is not None else len(flagged) + len(repaired)
    effective = max(1, pages - len(probe.blank_pages))
    # pages_flagged means "this engine is wrong for the file: use another one". With no engine left (forced engine or
    # the last fallback) every flagged page was sent to repair instead, and only pages still broken count.
    still_broken = sum(1 for e in flagged if not e.get("repaired_by"))
    if not quick and (still_broken if last_resort else n_flagged) > FLAGGED_MAX_RATIO * effective:
        reasons.append("pages_flagged")
    unrepaired = sum(1 for e in entries if not e.get("repaired_by"))
    # missing pages beyond the listed 200 still count as unrepaired
    unrepaired += max(0, (pm["expected"] - pm["found"]) - len(pm["missing"]))
    level = "low" if reasons else ("warn" if unrepaired else "ok")
    score = base.score * pm["coverage"] * max(0.0, 1 - unrepaired / effective)
    if vlm_pages and layers:
        entries = _vlm_advisories(markdown, entries, vlm_pages, layers, set(probe.broken_font_pages))
    return QualityResult(score=round(score, 3), level=level, reasons=reasons, metrics=base.metrics,
                         page_check=PAGE_CHECK_VERSION, page_map=pm, pages=entries, pages_flagged=n_flagged)
