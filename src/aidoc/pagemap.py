"""Page sections, page-map coverage, alignment spot check and page splicing (spec 2026-10-01 §6, §7, §8.2).

Page boundaries come from the engines' structure (runners); this module never searches markdown text to *find*
page boundaries — it only reads the `<!-- page: N -->` markers and checks them against the PDF text layer.
"""
from __future__ import annotations

import bisect
import random
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path

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


def squash(s: str) -> str:
    """NFKC, drop soft hyphens and line-break hyphenation, lower-case, keep only alphanumerics (any script)."""
    s = unicodedata.normalize("NFKC", s).replace("­", "").replace("-\n", "")
    return "".join(c for c in s.lower() if c.isalnum())


def pdf_text_layers(pdf: Path) -> list[str]:
    import pymupdf
    with pymupdf.open(pdf) as doc:
        return [squash(page.get_text()) for page in doc]


def _even_sample(items: list[int], k: int) -> list[int]:
    if k >= len(items):
        return list(items)
    return [items[(i * len(items)) // k] for i in range(k)]


def _probes(text: str, page_no: int, whole: str, win: int, k: int) -> list[str]:
    lo, hi = int(len(text) * 0.2), int(len(text) * 0.8) - win
    if hi <= lo:
        return []
    rng = random.Random(page_no)
    out: list[str] = []
    for _ in range(_CANDIDATE_STARTS):
        start = rng.randint(lo, hi)
        probe = text[start:start + win]
        if len(probe) == win and probe not in out and whole.count(probe) == 1:
            out.append(probe)
            if len(out) >= k:
                break
    return out


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
        else:
            misplaced.append(p)
    return {"sampled": len(picked), "decidable": decidable, "aligned": aligned,
            "ratio": round(aligned / decidable, 4) if decidable else None,
            "misplaced": misplaced[:MISPLACED_LIST_MAX], "excluded_pages": len(excl)}


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
