"""Quality assessment (spec §4 品質檢查). Thresholds are initial; tune on fixtures."""
from __future__ import annotations

import re
import unicodedata

from aidoc.models import ProbeResult, QualityResult

MIN_CHARS_PER_PAGE = 50
MAX_GARBAGE_RATIO = 0.05

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


def assess(markdown: str, probe: ProbeResult, pages: int | None) -> QualityResult:
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
