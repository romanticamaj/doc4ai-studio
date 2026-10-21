"""Text normalisation policy of the real-sample evaluation (spec 2026-10-22 §5.1). One policy for GT and prediction:
`norm_pred` = `bench.metrics.plain` (markup and math removed) followed by exactly the steps of `norm_gt`. The only
GT-only step is errata, applied to the scoring copy of the GT by `apply_errata` before `norm_gt` (§4.4, §6.1).

Order: math unwrapped (pred; amendment 2026-10-22) → plain (pred) → errata (GT) → Bopomofo tone marks removed → apostrophes unified → `aidoc.pagemap.squash`
(NFKC, soft hyphen / line-break hyphen, lower-case, alphanumerics of any script). No simplified/traditional or glyph
variant folding, no per-document filtering."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from rapidfuzz.distance import Levenshtein

from aidoc.pagemap import squash
from bench.metrics import _MATH as MATH_SPAN  # the span pattern plain() and formulas_in() use
from bench.metrics import plain

VARIANTS = Path(__file__).resolve().parent / "variants.json"
TONE_MARKS = "ˇˊˋ˙"                       # U+02C7, U+02CA, U+02CB, U+02D9
APOSTROPHES = {"’": "'", "ʼ": "'"}        # U+2019, U+02BC → U+0027
_TABLE = {ord(c): None for c in TONE_MARKS} | {ord(k): v for k, v in APOSTROPHES.items()}


def norm_gt(text: str) -> str:
    return squash(text.translate(_TABLE))


_CMD = re.compile(r"\\[A-Za-z]+\*?")        # \frac, \mathrm, \alpha, \text*: the command name goes
_ESC = re.compile(r"\\(?=[^A-Za-z])")        # \{, \%, \, : the backslash goes, the character stays


def _unwrap_span(m: re.Match) -> str:
    t = m.group(0)
    for a, b in (("$$", "$$"), ("$", "$"), ("\\[", "\\]"), ("\\(", "\\)")):
        if t.startswith(a) and t.endswith(b) and len(t) >= len(a) + len(b):
            t = t[len(a):len(t) - len(b)]
            break
    return " " + _ESC.sub("", _CMD.sub(" ", t)) + " "


def unwrap_math(markdown: str) -> str:
    r"""Spec amendment 2026-10-22 (§5.1 step 1): every `$$…$$`, `$…$`, `\[…\]`, `\(…\)` span keeps its
    content — delimiters and LaTeX command names removed, the arguments' characters kept — for every engine alike,
    so an engine that wraps digits or CJK in inline math is not penalised for it."""
    return MATH_SPAN.sub(_unwrap_span, markdown)


def norm_pred(markdown: str) -> str:
    return norm_gt(plain(unwrap_math(markdown)))


def apply_errata(gt_pages: dict[int, str], errata: list[dict], doc: str) -> dict[int, str]:
    """A new {page: text} with this document's errata applied; each `wrong` must occur exactly once on its page."""
    out = dict(gt_pages)
    for e in errata:
        if e["doc"] != doc:
            continue
        p = int(e["page"])
        if p not in out:
            raise ValueError(f"erratum for {doc} p{p}: page has no GT")
        n = out[p].count(e["wrong"])
        if n != 1:
            raise ValueError(f"erratum for {doc} p{p}: 'wrong' occurs {n} times (must be exactly once)")
        out[p] = out[p].replace(e["wrong"], e["right"])
    return out


_pairs: set[frozenset] | None = None
_t2s = False


def _variant_pairs() -> set[frozenset]:
    global _pairs, _t2s
    if _pairs is None:
        _pairs = {frozenset(p) for p in json.loads(VARIANTS.read_text(encoding="utf-8"))}
        try:                                    # optional diagnostic aid (Apache-2.0); absent → fixed table only
            import opencc  # type: ignore[import-not-found]
            _t2s = opencc.OpenCC("t2s")
        except Exception:  # noqa: BLE001
            _t2s = False
    return _pairs


def variant_share(gt: str, pred: str) -> float | None:
    """Diagnostic (§5.4): share of character substitutions (GT→pred, after normalisation) that are a known glyph
    variant or a traditional→simplified mapping; None when there is no substitution."""
    a, b = norm_gt(gt), norm_gt(pred)
    pairs = _variant_pairs()
    subs = Counter((a[op.src_pos], b[op.dest_pos]) for op in Levenshtein.editops(a, b) if op.tag == "replace")
    total = sum(subs.values())
    if not total:
        return None
    hit = 0
    for (x, y), n in subs.items():
        if frozenset((x, y)) in pairs or (_t2s and (_t2s.convert(x) == y or _t2s.convert(y) == x)):
            hit += n
    return hit / total
