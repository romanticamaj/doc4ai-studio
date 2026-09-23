"""Quality assessment (spec §4 品質檢查). Thresholds are initial; tune on fixtures."""
from __future__ import annotations

import re

from aidoc.models import ProbeResult, QualityResult

MIN_CHARS_PER_PAGE = 50
MAX_GARBAGE_RATIO = 0.05

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_SEP_LINE = re.compile(r"(?m)^\s*\|?\s*:?-{3,}[\s|:-]*$")
_MARKUP_CHARS = re.compile(r"[|#*_`$]")
_TABLE_GFM = re.compile(r"(?m)^\s*\|.*\|\s*$\n\s*\|?\s*:?-{3,}")
_TABLE_HTML = re.compile(r"<table", re.IGNORECASE)
_GARBAGE_RUN = re.compile(
    r"[^\w\s　-〿一-鿿＀-￯,.;:!?()\[\]{}'\"\-–—/|*#$%&+=<>@^_`~]{4,}")


def strip_markup(markdown: str) -> str:
    s = _COMMENT.sub("", markdown)
    s = _IMAGE.sub("", s)
    s = _SEP_LINE.sub("", s)
    return _MARKUP_CHARS.sub("", s)


def has_table(markdown: str) -> bool:
    return bool(_TABLE_GFM.search(markdown) or _TABLE_HTML.search(markdown))


def _is_garbage_char(c: str) -> bool:
    o = ord(c)
    if c == "�" or 0xE000 <= o <= 0xF8FF or o >= 0xF0000:
        return True
    return (o < 32 or 0x7F <= o < 0xA0) and c not in "\n\t\r"


def garbage_ratio(markdown: str) -> float:
    s = _COMMENT.sub("", markdown)
    non_space = [c for c in s if not c.isspace()]
    if not non_space:
        return 0.0
    flagged = [False] * len(s)
    for i, c in enumerate(s):
        if _is_garbage_char(c):
            flagged[i] = True
    for m in _GARBAGE_RUN.finditer(s):
        for i in range(m.start(), m.end()):
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
