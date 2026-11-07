"""Byte ranges pdf.js needs to open a PDF and draw its first pages (served as one bundle by the web API).

pdf.js reads a PDF with range requests, and every step that depends on the previous one costs a round trip: the
header chunk, the trailer, then each xref section of the /Prev chain (a book saved incrementally 32 times has 32),
a big xref table chunk by chunk, then the catalog, the page tree, each page, its contents and images. At ~1 s RTT
(Tailscale DERP) that is ~45 s before anything is drawn. The server reads the same structure locally in
milliseconds, so it lists those byte ranges, aligned to pdf.js' chunk size, and the browser fetches them in one
request and answers pdf.js from memory.

Everything here is a hint: a range left out is simply fetched by pdf.js as before, so parsing is best effort and
never raises (unknown formats get the header and trailer chunks only).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

MAX_SECTIONS = 512
TRAILER_SLACK = 4096           # bytes past "trailer" that hold the trailer dictionary
OBJECT_SLACK = 2048            # an object's dictionary when its length is unknown
_INT_KEY = r"/%s\s+(\d+)(?!\s+\d+\s+R)"


def open_ranges(path: Path, chunk: int, pages: int = 2, max_bytes: int = 16 * 1024 * 1024) -> list[tuple[int, int]]:
    """Sorted, merged, chunk-aligned [begin, end) ranges, at most `max_bytes` in total (most important first:
    header, trailer, xref sections newest first, then the objects of the first `pages` pages + one page dict)."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size == 0 or chunk <= 0:
        return []
    wanted: list[tuple[int, int]] = [(0, 1), (max(0, size - 1024), size)]
    try:
        with path.open("rb") as f:
            sections, offsets = _xref_sections(f, size)
            wanted += sections
            wanted += _page_objects(path, offsets, pages)
    except Exception:  # noqa: BLE001, S110  a hint must never fail the request (pdf.js fetches what is missing)
        pass
    return _align(wanted, chunk, size, max_bytes)


def _read(f, pos: int, n: int) -> bytes:
    f.seek(pos)
    return f.read(n)


def _xref_sections(f, size: int) -> tuple[list[tuple[int, int]], dict[int, int]]:
    """Ranges of every xref section (classic table + trailer, or xref stream object) of the /Prev chain, newest
    first, and the byte offset of each object listed in classic tables (the newest definition wins)."""
    tail = _read(f, max(0, size - 2048), 2048)
    m = list(re.finditer(rb"startxref\s+(\d+)", tail))
    if not m:
        return [], {}
    todo, seen = [int(m[-1].group(1))], set()
    ranges: list[tuple[int, int]] = []
    offsets: dict[int, int] = {}
    while todo and len(seen) < MAX_SECTIONS:
        pos = todo.pop(0)
        if pos in seen or not 0 <= pos < size:
            continue
        seen.add(pos)
        head = _read(f, pos, 64)
        if head.lstrip().startswith(b"xref"):
            end, trailer = _classic_section(f, pos, size, offsets)
        else:
            end, trailer = _stream_section(f, pos, size)
        if end is None:
            continue
        ranges.append((pos, end))
        for key in (b"XRefStm", b"Prev"):           # hybrid files: the xref stream is read before /Prev
            k = re.search(_INT_KEY.encode() % key, trailer)
            if k:
                todo.append(int(k.group(1)))
    return ranges, offsets


def _find(f, needle: bytes, pos: int, size: int, limit: int) -> int:
    block, keep = 1 << 20, len(needle)
    at = pos
    while at < min(size, pos + limit):
        data = _read(f, at, block + keep)
        i = data.find(needle)
        if i >= 0:
            return at + i
        at += block
    return -1


def _classic_section(f, pos: int, size: int, offsets: dict[int, int]):
    t = _find(f, b"trailer", pos, size, 64 << 20)
    if t < 0:
        return None, b""
    table = _read(f, pos, t - pos)
    tokens = table.split()[1:]                      # after "xref": (start count) then count x (offset gen n|f)
    i = 0
    while i + 1 < len(tokens):
        start, count = int(tokens[i]), int(tokens[i + 1])
        i += 2
        for k in range(count):
            if i + 2 >= len(tokens):            # truncated table: keep what was read
                i = len(tokens)
                break
            off, kind = tokens[i], tokens[i + 2]
            if kind == b"n":
                offsets.setdefault(start + k, int(off))
            i += 3
    trailer = _read(f, t, TRAILER_SLACK)
    s = trailer.find(b"startxref")
    end = t + (s if s >= 0 else len(trailer))
    return end, trailer[: s if s >= 0 else None]


def _stream_section(f, pos: int, size: int):
    head = _read(f, pos, TRAILER_SLACK)
    if not re.match(rb"\s*\d+\s+\d+\s+obj", head):
        return None, b""
    s = head.find(b"stream")
    length = re.search(_INT_KEY.encode() % b"Length", head[: s if s >= 0 else None])
    if s >= 0 and length:
        end = pos + s + len(b"stream") + 2 + int(length.group(1)) + 32
    else:
        e = _find(f, b"endobj", pos, size, 64 << 20)
        end = e + 6 if e >= 0 else pos + TRAILER_SLACK
    return min(end, size), head[: s if s >= 0 else None]


def _page_objects(path: Path, offsets: dict[int, int], pages: int) -> list[tuple[int, int]]:
    """Catalog, page tree, the dicts of pages 1..pages+1 and the contents, images and fonts of pages 1..pages
    (only objects at a known offset: those in classic xref tables, not inside object streams)."""
    if not offsets:
        return []
    import pymupdf
    out: list[tuple[int, int]] = []
    doc = pymupdf.open(path)
    try:
        def add(xref: int) -> None:
            off = offsets.get(xref)
            if off is None:
                return
            n = OBJECT_SLACK
            kind, val = doc.xref_get_key(xref, "Length")
            if kind == "int":
                n += int(val)
            out.append((off, off + n))

        def ref(xref: int, key: str) -> int | None:
            kind, val = doc.xref_get_key(xref, key)
            return int(val.split()[0]) if kind == "xref" else None

        cat = doc.pdf_catalog()
        add(cat)
        for n in range(min(pages + 1, doc.page_count)):
            page = doc[n]
            parent, hops = ref(page.xref, "Parent"), 0
            while parent and hops < 32:             # the page tree path to this page
                add(parent)
                parent, hops = ref(parent, "Parent"), hops + 1
            add(page.xref)
            if n >= pages:
                continue
            res = ref(page.xref, "Resources")
            if res:
                add(res)
            for x in _all(page.get_contents()):
                add(x)
            for img in page.get_images(full=True):
                add(img[0])
                if img[1]:
                    add(img[1])                     # soft mask
            for font in page.get_fonts(full=True):
                add(font[0])
                desc = ref(font[0], "FontDescriptor")
                if desc:
                    add(desc)
                    for key in ("FontFile", "FontFile2", "FontFile3"):
                        ff = ref(desc, key)
                        if ff:
                            add(ff)
    finally:
        doc.close()
    return out


def _all(xs: Iterable[int]) -> list[int]:
    return [x for x in xs if x > 0]


def _align(wanted: list[tuple[int, int]], chunk: int, size: int, max_bytes: int) -> list[tuple[int, int]]:
    taken: set[int] = set()
    budget = max_bytes
    for b, e in wanted:                             # in priority order, until the budget is spent
        if e <= b:
            continue
        for c in range(b // chunk, (min(e, size) - 1) // chunk + 1):
            if c in taken:
                continue
            n = min(size, (c + 1) * chunk) - c * chunk
            if n > budget:
                break
            taken.add(c)
            budget -= n
    out: list[tuple[int, int]] = []
    for c in sorted(taken):
        b, e = c * chunk, min(size, (c + 1) * chunk)
        if out and out[-1][1] == b:
            out[-1] = (out[-1][0], e)
        else:
            out.append((b, e))
    return out
