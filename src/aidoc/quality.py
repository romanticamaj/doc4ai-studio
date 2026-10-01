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
FLAGGED_MAX_RATIO = 0.20
PAGE_GARBAGE_MAX = 0.05
PAGE_GARBAGE_MIN_CHARS = 50
SHIFT_HITS_MIN = 3
PAGE_CHECK_VERSION = 1
SHIFT = 29                     # TrueType standard Mac glyph order: glyph id = char code - 29 (glyph 3 = space)
COMMON_WORDS = frozenset(
    "the and you that it is to of in for on with this be are your can if what there but was as at by from or an "
    "not have will all so do just like how app".split())

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

_FFFD_BETWEEN = re.compile(r"(?<=\S)�(?=\S)")
_ASCII_RUN = re.compile(r"[!-~]+")


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


def assess(markdown: str, probe: ProbeResult, pages: int | None, *, pdf: Path | None = None,
           page_map_method: str | None = None, quick: bool = False, flagged_before: int | None = None,
           repaired: list[dict] | None = None, align_sample: int | None = None,
           layers: list[str] | None = None) -> QualityResult:
    """Document quality. Legacy rules for non-PDFs; PDFs add the page map, alignment spot check and flagged pages
    (spec 2026-10-01 §5.2: any document reason -> low; else unrepaired flagged pages -> warn; else ok)."""
    base = _assess_base(markdown, probe, pages)
    base.page_check = PAGE_CHECK_VERSION
    if probe.kind != "pdf" or pages is None:
        return base
    reasons = list(base.reasons)
    pm = pagemap.page_map(markdown, pages, page_map_method)
    if pm["coverage"] < COVERAGE_MIN:
        reasons.append("page_map_incomplete")
    if pdf is not None:
        al = pagemap.spot_check(markdown, pdf, sample=align_sample or (10 if quick else 30),
                                exclude=probe.broken_font_pages, layers=layers)
        pm["alignment"] = al
        if al["decidable"] >= ALIGN_MIN_DECIDABLE and al["ratio"] is not None and al["ratio"] < ALIGN_MIN:
            reasons.append("page_map_misaligned")
    repaired = list(repaired or [])
    repaired_pages = {e["page"] for e in repaired}
    flagged = [e for e in flag_pages(markdown, probe.broken_font_pages) if e["page"] not in repaired_pages]
    missing = [{"page": p, "reasons": ["page_map_missing"]} for p in pm["missing"]]
    entries = sorted(flagged + repaired + missing, key=lambda e: e["page"])
    n_flagged = flagged_before if flagged_before is not None else len(flagged) + len(repaired)
    effective = max(1, pages - len(probe.blank_pages))
    if not quick and n_flagged > FLAGGED_MAX_RATIO * effective:
        reasons.append("pages_flagged")
    unrepaired = sum(1 for e in entries if not e.get("repaired_by"))
    # missing pages beyond the listed 200 still count as unrepaired
    unrepaired += max(0, (pm["expected"] - pm["found"]) - len(pm["missing"]))
    level = "low" if reasons else ("warn" if unrepaired else "ok")
    score = base.score * pm["coverage"] * max(0.0, 1 - unrepaired / effective)
    return QualityResult(score=round(score, 3), level=level, reasons=reasons, metrics=base.metrics,
                         page_check=PAGE_CHECK_VERSION, page_map=pm, pages=entries, pages_flagged=n_flagged)
