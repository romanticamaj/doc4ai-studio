"""RAG chunking (spec §5 「RAG 切段」): heading-aware chunks with page ranges, tables/math/code never split."""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from aidoc import fsops, paths
from aidoc.output import list_output_dirs, read_sidecar

Counter = Callable[[str], int]

_PAGE = re.compile(r"^<!-- page: (\d+) -->$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^(`{3,}|~{3,})")
_encoder = None


class TokenizerUnavailable(RuntimeError):
    """cl100k_base is not cached and cannot be downloaded (offline / proxy)."""


def count_tokens(text: str) -> int:
    """tiktoken cl100k_base (BPE cached under data/tiktoken by `aidoc setup`)."""
    global _encoder
    if _encoder is None:
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(paths.data_dir() / "tiktoken"))
        try:
            import tiktoken
            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception as e:  # noqa: BLE001  requests.ProxyError, ConnectionError, ...
            raise TokenizerUnavailable(
                f"the cl100k_base tokenizer is not cached in {os.environ['TIKTOKEN_CACHE_DIR']} and could not be "
                f"downloaded ({type(e).__name__}); run `aidoc setup markitdown` (or any engine) once with network "
                f"access to cache it") from e
    return len(_encoder.encode(text, disallowed_special=()))


@dataclass
class Chunk:
    id: str
    source: str
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    text: str
    oversized: bool = False

    def to_json(self) -> dict:
        d = {"id": self.id, "source": self.source, "heading_path": list(self.heading_path),
             "page_start": self.page_start, "page_end": self.page_end, "text": self.text}
        if self.oversized:
            d["oversized"] = True
        return d


@dataclass
class _Block:
    kind: str                       # "heading" | "text"
    text: str
    page: int | None
    level: int = 0
    pages: list[int] = field(default_factory=list)
    pre: bool = False               # before the first page marker of a paged document


def _blocks(md: str) -> list[_Block]:
    """Split markdown into headings and atomic content blocks, tracking the page of each."""
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[_Block] = []
    i, n = 0, len(lines)
    first_marker = next((int(m.group(1)) for ln in lines if (m := _PAGE.match(ln.strip()))), None)
    # text before the first marker: page 1 when only the page-1 marker is missing, otherwise unknown
    page: int | None = 1 if first_marker == 2 else None
    seen_marker = False

    def close_at(start: int, closes, stop_blank: bool = False) -> int:
        """Index of the block's last line. An unclosed block ends before the next heading (or blank line)
        instead of swallowing the rest of the document."""
        j = start
        while j < n:
            t = lines[j].strip()
            if closes(t):
                return j
            if _HEADING.match(t) or (stop_blank and not t):
                return j - 1
            j += 1
        return n - 1

    def add(block_lines: list[str], first_page: int | None) -> None:
        """A block spanning a page break (HTML table, math) covers the later pages too; markers are dropped."""
        nonlocal page
        kept, pages = [], [first_page]
        for ln in block_lines:
            pm = _PAGE.match(ln.strip())
            if pm:
                page = int(pm.group(1))
                pages.append(page)
            else:
                kept.append(ln)
        out.append(_Block("text", "\n".join(kept), first_page, pages=[q for q in pages if q is not None],
                          pre=first_marker is not None and not seen_marker))

    while i < n:
        line = lines[i]
        s = line.strip()
        m = _PAGE.match(s)
        if m:
            page = int(m.group(1))
            seen_marker = True
            i += 1
            continue
        if not s:
            i += 1
            continue
        h = _HEADING.match(s)
        if h:
            out.append(_Block("heading", h.group(2).strip(), page, level=len(h.group(1)),
                              pages=[page] if page is not None else [],
                              pre=first_marker is not None and not seen_marker))
            i += 1
            continue
        start_page = page
        f = _FENCE.match(s)
        if f:                                            # fenced code: up to the closing fence
            fence = f.group(1)[0] * len(f.group(1))
            j = close_at(i + 1, lambda t: t.startswith(fence))
            out.append(_Block("text", "\n".join(lines[i:j + 1]), start_page,
                              pages=[start_page] if start_page is not None else [],
                              pre=first_marker is not None and not seen_marker))
            i = j + 1
            continue
        if s.startswith("$$"):                           # display math
            j = i
            if not (len(s) > 2 and s.endswith("$$")):
                j = close_at(i + 1, lambda t: "$$" in t, stop_blank=True)
            add(lines[i:j + 1], start_page)
            i = j + 1
            continue
        if s.lower().startswith("<table"):               # HTML table, may contain blank lines
            j = close_at(i, lambda t: "</table>" in t.lower())
            add(lines[i:j + 1], start_page)
            i = j + 1
            continue
        if s.startswith("|"):                            # GFM table: consecutive pipe rows
            j = i
            while j < n and lines[j].strip().startswith("|"):
                j += 1
            add(lines[i:j], start_page)
            i = j
            continue
        j = i                                            # paragraph: until a blank line, page marker or block
        while j < n:
            t = lines[j].strip()
            if j > i and (not t or _PAGE.match(t) or _HEADING.match(t) or _FENCE.match(t)
                          or t.startswith(("$$", "|")) or t.lower().startswith("<table")):
                break
            j += 1
        add(lines[i:j], start_page)
        i = j
    return out


def chunk_markdown(md: str, source: str, max_tokens: int = 800, counter: Counter = count_tokens,
                   stem: str | None = None) -> list[Chunk]:
    stem = stem or Path(source).stem
    chunks: list[Chunk] = []
    path: list[list] = []            # [level, text, has_content, heading block]
    cur: list[_Block] = []
    cur_path: list[str] = []

    def emit(blocks: list[_Block], heading_path: list[str], oversized: bool = False) -> None:
        pages = [p for b in blocks for p in b.pages]
        chunks.append(Chunk(id=f"{stem}#{len(chunks):04d}", source=source, heading_path=list(heading_path),
                            page_start=min(pages) if pages else None, page_end=max(pages) if pages else None,
                            text="\n\n".join(b.text for b in blocks), oversized=oversized))

    def flush() -> None:
        nonlocal cur
        if cur:
            emit(cur, cur_path)
            cur = []

    def empty_leaf() -> None:
        """A heading with nothing under it (not even sub-headings) still becomes a chunk of its own."""
        level, text, has_content, hb = path[-1]
        if not has_content:
            flush()
            emit([_Block("text", "#" * level + " " + text, hb.page, pages=hb.pages)], [e[1] for e in path])

    for b in _blocks(md):
        if b.kind == "heading":
            flush()
            if path and path[-1][0] >= b.level:
                empty_leaf()
            while path and path[-1][0] >= b.level:
                path.pop()
            for entry in path:
                entry[2] = True                          # a parent with a sub-heading is not empty
            path.append([b.level, b.text, False, b])
            continue
        for entry in path:
            entry[2] = True
        heading_path = [e[1] for e in path]
        if heading_path != cur_path or (cur and cur[-1].pre and not b.pre):
            flush()
            cur_path = heading_path
        if counter(b.text) > max_tokens:                 # a block never gets split: alone and flagged
            flush()
            emit([b], cur_path, oversized=True)
            continue
        if cur and counter("\n\n".join([*(x.text for x in cur), b.text])) > max_tokens:
            flush()
        cur.append(b)
    flush()
    if path:
        empty_leaf()
    return chunks


def _chunk_dirs(out_root: Path, max_tokens: int, only: str | None, counter: Counter) -> tuple[Path, int]:
    out_root = Path(out_root)
    lines: list[str] = []
    for d in list_output_dirs(out_root):
        if only is not None and d.name != only:
            continue
        md = d / f"{d.name}.md"
        if not md.is_file():
            continue
        sc = read_sidecar(d) or {}
        source = str(sc.get("source") or md.name)
        for c in chunk_markdown(md.read_text(encoding="utf-8"), source, max_tokens, counter, stem=d.name):
            lines.append(json.dumps(c.to_json(), ensure_ascii=False))
    target = out_root / "chunks.jsonl"
    written = len(lines)
    if only is not None and target.is_file():       # refresh one document, keep every other document's chunks
        kept = []
        for line in target.read_text(encoding="utf-8").splitlines():
            try:
                cid = str(json.loads(line).get("id", ""))
            except ValueError:
                continue
            if cid.rpartition("#")[0] != only:
                kept.append(line)
        lines = kept + lines
    fsops.atomic_write_lines(target, lines)
    return target, written


def chunk_output_dir(out_root: Path, max_tokens: int = 800, only: str | None = None,
                     counter: Counter = count_tokens) -> Path:
    """Chunk every converted document under out_root into out_root/chunks.jsonl (written atomically).
    With `only`, just that document's chunks are replaced; the other documents' lines are kept."""
    return _chunk_dirs(out_root, max_tokens, only, counter)[0]
