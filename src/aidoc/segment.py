"""PDF segmentation (spec §8.3): plan 40-page segments, split physically with PyMuPDF, merge outputs."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz

from aidoc.models import SEGMENT_PAGES, NormalizedResult, TableEdge

_MARKER_LINE = re.compile(r"^<!-- page: \d+ -->$")
_SEP_CELL = re.compile(r"^:?-{3,}:?$")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")


def plan_segments(pages: int | None) -> list[tuple[int | None, int | None]]:
    """1-based inclusive page ranges; page-less inputs are one segment (None, None)."""
    if not pages:
        return [(None, None)]
    return [(a, min(a + SEGMENT_PAGES - 1, pages)) for a in range(1, pages + 1, SEGMENT_PAGES)]


def split_pdf(src: Path, page_start: int, page_end: int, dst: Path) -> Path:
    """Write pages page_start..page_end (1-based, inclusive) of src into dst."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.tmp")
    with fitz.open(str(src)) as doc, fitz.open() as out:
        out.insert_pdf(doc, from_page=page_start - 1, to_page=page_end - 1)
        out.save(str(tmp), garbage=3, deflate=True)
    tmp.replace(dst)
    return dst


def extract_pages(src: Path, pages: list[int], dst: Path) -> Path:
    """Write the given 1-based pages of src, in that order, into dst (per-page repair, spec 2026-10-01 §8.2)."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.tmp")
    with fitz.open(str(src)) as doc, fitz.open() as out:
        for p in pages:
            out.insert_pdf(doc, from_page=p - 1, to_page=p - 1)
        out.save(str(tmp), garbage=3, deflate=True)
    tmp.replace(dst)
    return dst


def segment_dir(work_dir: Path, idx: int) -> Path:
    return Path(work_dir) / f"seg_{idx}"


@dataclass
class SegmentPart:
    idx: int
    page_start: int | None
    page_end: int | None
    markdown: str                      # normalised (absolute page markers, assets/<name> links)
    assets: list[tuple[Path, str]] = field(default_factory=list)
    has_page_markers: bool = False
    first_table: TableEdge | None = None
    last_table: TableEdge | None = None
    page_count: int | None = None
    engine: str | None = None          # engine that produced this part (resume only reuses the same engine)
    opts_key: str | None = None        # output-affecting options it was made with (resume needs the same)
    page_map_method: str | None = None  # how the runner produced the page markers (index A20)
    failed_pages: dict[int, str] = field(default_factory=dict)  # absolute page -> engine error (partial success)
    images_missing: dict[int, int] = field(default_factory=dict)  # absolute page -> pictures the engine lost


def _cells(line: str) -> list[str]:
    s = line.strip()
    s = s.removeprefix("|")
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip() for c in _CELL_SPLIT.split(s)]


def _is_table_line(line: str) -> bool:
    return line.lstrip().startswith("|")


def _is_sep(line: str) -> bool:
    cells = _cells(line)
    return bool(cells) and all(_SEP_CELL.match(c) for c in cells)


def _gfm_cols(rows: list[str]) -> int | None:
    """Column count when rows are a well-formed GFM table (header, separator, body), else None."""
    if len(rows) < 2 or not _is_sep(rows[1]):
        return None
    n = len(_cells(rows[0]))
    if len(_cells(rows[1])) != n or any(len(_cells(r)) != n for r in rows[2:]):
        return None
    return n


def split_trailing_gfm_table(md: str) -> tuple[str, list[str]]:
    """(text before, table lines) when md ends with a GFM table (trailing blank lines ignored), else (md, [])."""
    lines = md.rstrip("\n").split("\n")
    i = len(lines)
    while i > 0 and _is_table_line(lines[i - 1]):
        i -= 1
    rows = lines[i:]
    if _gfm_cols(rows) is None:
        return md, []
    before = "\n".join(lines[:i])
    return (before + "\n" if before else ""), rows


def split_leading_gfm_table(md: str) -> tuple[list[str], str]:
    """(table lines, rest) when md starts with a GFM table, skipping a first page-marker line and blank lines.

    The skipped marker is kept at the start of `rest` so the caller can put it after the merged table
    (a marker inside a GFM table would break it)."""
    lines = md.split("\n")
    i, marker = 0, None
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and _MARKER_LINE.match(lines[i].strip()):
        marker = lines[i].strip()
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    j = i
    while j < len(lines) and _is_table_line(lines[j]):
        j += 1
    rows = lines[i:j]
    if _gfm_cols(rows) is None:
        return [], md
    rest = "\n".join(lines[j:]).lstrip("\n")
    if marker is not None:
        rest = marker + "\n" + rest
    return rows, rest


def merge_edge_tables(prev_md: str, next_md: str, prev: SegmentPart, nxt: SegmentPart) -> tuple[str, str] | None:
    """Join a table cut by the segment boundary (spec §8.3). Conservative: every piece of evidence must agree."""
    lt, ft = prev.last_table, nxt.first_table
    if lt is None or ft is None or not lt.touches_edge or not ft.touches_edge:
        return None
    if prev.page_count is None or lt.page != prev.page_count or ft.page != 1 or lt.n_cols != ft.n_cols:
        return None
    before, tail = split_trailing_gfm_table(prev_md)
    head, rest = split_leading_gfm_table(next_md)
    if not tail or not head or _gfm_cols(tail) != lt.n_cols or _gfm_cols(head) != lt.n_cols:
        return None
    continuation = head[2:]
    if _cells(head[0]) != _cells(tail[0]):
        continuation = [head[0]] + continuation          # a different header is data, not a repeated header
    merged = before + "\n".join(tail + continuation) + "\n"
    return merged, rest


def merge_segments(parts: list[SegmentPart]) -> NormalizedResult:
    """Concatenate normalised segment outputs in order; segment start pages are known facts."""
    mds: list[str] = []
    for p in parts:
        md = p.markdown
        if not p.has_page_markers and p.page_start is not None:
            md = f"<!-- page: {p.page_start} -->\n" + md.lstrip("\n")
        mds.append(md)
    for i in range(1, len(parts)):
        joined = merge_edge_tables(mds[i - 1], mds[i], parts[i - 1], parts[i])
        if joined is not None:
            mds[i - 1], mds[i] = joined
    body = "\n\n".join(m.strip("\n") for m in mds if m.strip())
    assets = [a for p in parts for a in p.assets]
    return NormalizedResult(markdown=body + "\n" if body else "", assets=assets)
