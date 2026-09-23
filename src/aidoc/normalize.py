"""Unify engine output: absolute page markers, asset names, GFM tables, whitespace (spec §3, §5)."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from aidoc.models import NormalizedResult, RawResult

PAGE_RE = re.compile(r"<!-- page: (\d+) -->")
IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
TABLE_RE = re.compile(r"<table\b.*?</table>", re.DOTALL | re.IGNORECASE)


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.header_row: int | None = None
        self.depth = 0
        self.bad = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._cell_is_th = False
        self._row_has_th = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self.depth += 1
            if self.depth > 1:
                self.bad = True
        elif tag == "tr":
            self._row = []
            self._row_has_th = False
        elif tag in ("td", "th"):
            if any(k.lower() in ("colspan", "rowspan") and str(v).strip() not in ("", "1") for k, v in attrs):
                self.bad = True
            self._cell = []
            self._cell_is_th = tag == "th"
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "table":
            self.depth -= 1
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._row_has_th = self._row_has_th or self._cell_is_th
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._cell is not None:            # unclosed cell
                self._row.append(" ".join("".join(self._cell).split()))
                self._cell = None
            if self._row_has_th and self.header_row is None:
                self.header_row = len(self.rows)
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def html_table_to_gfm(html: str) -> str | None:
    p = _TableParser()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001  malformed HTML of any kind -> keep the table as HTML
        return None
    rows = p.rows
    if p.bad or not rows:
        return None
    n = len(rows[0])
    if n == 0 or any(len(r) != n for r in rows):
        return None
    hi = p.header_row if p.header_row is not None else 0
    header, body = rows[hi], rows[:hi] + rows[hi + 1:]

    def line(cells):
        return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"
    return "\n".join([line(header), "| " + " | ".join(["---"] * n) + " |"] + [line(r) for r in body])


def _convert_tables(md: str) -> str:
    def sub(m):
        g = html_table_to_gfm(m.group(0))
        return f"\n\n{g}\n\n" if g is not None else m.group(0)
    return TABLE_RE.sub(sub, md)


def _clean_ws(md: str) -> str:
    md = md.replace("\r\n", "\n").replace("\r", "\n")
    md = "\n".join(line.rstrip() for line in md.split("\n"))
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip("\n") + "\n"


def normalize(raw: RawResult, page_offset: int, seg_idx: int) -> NormalizedResult:
    md = raw.markdown
    by_name: dict[str, Path] = {}
    for p in raw.image_paths:
        by_name.setdefault(Path(p).name, Path(p))

    # page of each position (relative, before offset)
    markers = [(m.start(), int(m.group(1))) for m in PAGE_RE.finditer(md)] if raw.has_page_markers else []

    def page_at(pos: int) -> int | None:
        page = None
        for at, n in markers:
            if at > pos:
                break
            page = n
        return None if page is None else page + page_offset

    counters: dict[object, int] = {}
    assigned: dict[Path, str] = {}
    assets: list[tuple[Path, str]] = []

    def name_for(src: Path, page: int | None) -> str:
        if src in assigned:
            return assigned[src]
        key = page if page is not None else ("s", seg_idx)
        counters[key] = counters.get(key, 0) + 1
        prefix = f"p{page}" if page is not None else f"s{seg_idx}"
        name = f"{prefix}_{counters[key]}{src.suffix.lower()}"
        assigned[src] = name
        assets.append((src, name))
        return name

    def sub_img(m):
        target = m.group(2)
        base = Path(unquote(target.split("?")[0].split("#")[0])).name
        src = by_name.get(base)
        if src is None:
            return m.group(0)
        return f"![{m.group(1)}](assets/{name_for(src, page_at(m.start()))})"

    md = IMG_RE.sub(sub_img, md)
    for p in raw.image_paths:                      # unreferenced images are kept as assets, unlinked
        if Path(p) not in assigned:
            name_for(Path(p), None)

    if page_offset:
        md = PAGE_RE.sub(lambda m: f"<!-- page: {int(m.group(1)) + page_offset} -->", md)
    md = _convert_tables(md)
    return NormalizedResult(markdown=_clean_ws(md), assets=assets)
