"""Document access shared by tools and resources: a small cache of parsed Markdown, asset URI rewriting, page
warnings (spec 2026-10-01 per-page quality) and the "stale while reconverting" check (MCP spec §9)."""
from __future__ import annotations

import difflib
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from aidoc import chunk as _chunk
from aidoc.mcp.budget import estimate_tokens
from aidoc.mcp.errors import ToolFailure
from aidoc.mcp.schemas import OutlineItem, PageWarning, TokenEstimate, TokenRange
from aidoc.pagemap import split_pages

VISIBLE = ("ok", "warn", "low")
_ASSET_MD = re.compile(r"\]\((assets/[^)\s]+)\)")
_ASSET_HTML = re.compile(r'src="(assets/[^"]+)"')
_CACHE_MAX = 8
_cache: OrderedDict[str, DocView] = OrderedDict()
_cache_lock = threading.Lock()


@dataclass
class DocView:
    row: dict
    md_path: Path
    markdown: str
    pre: str
    sections: dict[int, str]
    mtime_ns: int
    size: int

    @property
    def doc_id(self) -> str:
        return self.row["id"]

    @property
    def title(self) -> str:
        return Path(self.row["output_dir"]).name

    @property
    def source_name(self) -> str:
        return Path(str(self.row["source_path"]).replace("\\", "/")).name or self.title

    @property
    def has_pages(self) -> bool:
        return bool(self.sections)

    @property
    def pages(self) -> int | None:
        return self.row.get("pages") or (max(self.sections) if self.sections else None)

    def page_text(self, page: int) -> str | None:
        return self.sections.get(page)

    def page_block(self, page: int) -> str:
        text = self.sections.get(page)
        if text is None:
            return f"<!-- page: {page} -->\n<!-- no text for this page -->\n"
        return f"<!-- page: {page} -->\n{text.lstrip(chr(10))}"


def doc_row(ctx, doc_id: str) -> dict:
    row = ctx.store.get_document(doc_id) if isinstance(doc_id, str) and doc_id else None
    if row is None or row["status"] not in VISIBLE:
        raise ToolFailure("document_not_found", f"no document with id {doc_id!r}",
                          hint="use list_documents or search_library to find valid doc_ids")
    return row


def load_doc(ctx, doc_id: str) -> DocView:
    row = doc_row(ctx, doc_id)
    out = Path(row["output_dir"])
    md_path = out / f"{out.name}.md"
    try:
        st = md_path.stat()
    except OSError:
        raise ToolFailure("output_missing", f"the converted output of {out.name!r} is no longer on disk",
                          hint="reconvert_document can rebuild it when the source is still available") from None
    with _cache_lock:
        v = _cache.get(doc_id)
        if v is not None and v.mtime_ns == st.st_mtime_ns and v.size == st.st_size:
            _cache.move_to_end(doc_id)
            v.row = row
            return v
    text = md_path.read_text(encoding="utf-8")
    pre, secs = split_pages(text)
    v = DocView(row=row, md_path=md_path, markdown=text, pre=pre, sections=secs, mtime_ns=st.st_mtime_ns, size=st.st_size)
    with _cache_lock:
        _cache[doc_id] = v
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return v


def resources_for(doc_id: str) -> list[str]:
    base = f"doc4ai://documents/{doc_id}"
    return [base, f"{base}/metadata", f"{base}/pages/{{range}}", f"{base}/assets/{{name}}"]


def rewrite_assets(md: str, doc_id: str) -> str:
    base = f"doc4ai://documents/{doc_id}/"
    md = _ASSET_MD.sub(lambda m: f"]({base}{m.group(1)})", md)
    return _ASSET_HTML.sub(lambda m: f'src="{base}{m.group(1)}"', md)


def page_warnings(row: dict, start: int | None = None, end: int | None = None) -> list[PageWarning]:
    out = []
    for p in (row.get("quality") or {}).get("pages") or []:
        page = p.get("page")
        if page is None or (start is not None and page < start) or (end is not None and page > end):
            continue
        reasons = list(p.get("reasons") or [])
        out.append(PageWarning(page=page, reason=reasons[0] if reasons else "flagged", reasons=reasons,
                               repaired=bool(p.get("repaired_by"))))
    return sorted(out, key=lambda w: w.page)


def flagged_count(row: dict) -> int:
    return len((row.get("quality") or {}).get("pages") or [])


def stale_job(ctx, row: dict) -> str | None:
    live = {"queued", "probing", "converting", "checking"}
    for t in ctx.store.list_tasks(status_in=sorted(live)):
        if t["sha256"] == row["sha256"] and t["output_dir"] == row["output_dir"]:
            return t["job_id"]
    return None


def visible_docs(store) -> list[dict]:
    return [d for d in store.list_documents() if d["status"] in VISIBLE]


OUTLINE_MAX = 200
RANGE_PAGES = 20
FLAGGED_MAX = 200
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^(`{3,}|~{3,})")
_MARKER = re.compile(r"^<!-- page: (\d+) -->$")
_est_cache: dict[tuple, TokenEstimate] = {}
_chunk_cache: dict[tuple, list] = {}


def outline(view: DocView, limit: int = OUTLINE_MAX) -> tuple[list[OutlineItem], bool]:
    items: list[OutlineItem] = []
    page: int | None = None
    fence: str | None = None
    for line in view.markdown.splitlines():
        m = _MARKER.match(line.strip())
        if m:
            page = int(m.group(1))
            continue
        f = _FENCE.match(line)
        if f:
            if fence is None:
                fence = f.group(1)[0]
            elif line.strip().startswith(fence * 3):
                fence = None
            continue
        if fence is not None:
            continue
        h = _HEADING.match(line)
        if h:
            if len(items) >= limit:
                return items, True
            items.append(OutlineItem(level=len(h.group(1)), title=h.group(2).strip(), page=page))
    return items, False


def _identity(view: DocView) -> tuple:
    return (view.doc_id, view.mtime_ns, view.size)


def token_ranges(view: DocView) -> TokenEstimate:
    key = _identity(view)
    with _cache_lock:
        hit = _est_cache.get(key)
    if hit is not None:
        return hit
    if not view.has_pages:
        total, method = estimate_tokens(view.markdown)
        est = TokenEstimate(total=total, per_page_avg=None, ranges=[TokenRange(pages="all", tokens=total)], method=method)
    else:
        last = max(view.sections)
        ranges, total, method = [], 0, "tiktoken"
        pre_tokens, method = estimate_tokens(view.pre) if view.pre.strip() else (0, method)
        for start in range(1, last + 1, RANGE_PAGES):
            end = min(start + RANGE_PAGES - 1, last)
            text = "".join(view.sections.get(p, "") for p in range(start, end + 1))
            n, method = estimate_tokens(text)
            if start == 1:
                n += pre_tokens
            ranges.append(TokenRange(pages=f"{start}-{end}" if end > start else str(start), tokens=n))
            total += n
        est = TokenEstimate(total=total, per_page_avg=total // last if last else None, ranges=ranges, method=method)
    with _cache_lock:                              # tools run in worker threads: evict under the lock
        _est_cache[key] = est
        while len(_est_cache) > 32:
            _est_cache.pop(next(iter(_est_cache)), None)
    return est


def chunk_cache(view: DocView, max_tokens: int) -> list:
    key = (*_identity(view), max_tokens)
    with _cache_lock:
        hit = _chunk_cache.get(key)
    if hit is not None:
        return hit
    try:
        chunks = _chunk.chunk_markdown(view.markdown, _chunk.source_label(view.source_name), max_tokens,
                                       _chunk.count_tokens, stem=view.title)
    except _chunk.TokenizerUnavailable as e:
        raise ToolFailure("tokenizer_unavailable", str(e), hint="run `aidoc setup` once with network access") from None
    with _cache_lock:                              # never read back after eviction: return the local list
        _chunk_cache[key] = chunks
        while len(_chunk_cache) > 16:
            _chunk_cache.pop(next(iter(_chunk_cache)), None)
    return chunks


_RANGE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")


def parse_pages(spec: str, total: int | None) -> tuple[int, int]:
    m = _RANGE.match(spec or "")
    hint = f"use get_document_info; this document has {total} pages" if total else "this document has no page markers"
    if not m:
        raise ToolFailure("page_range_invalid", f"pages must look like \"12\" or \"12-15\", got {spec!r}", hint=hint, pages=total)
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) else a
    if a < 1 or b < a or (total is not None and b > total):
        raise ToolFailure("page_range_invalid", f"page range {a}-{b} is outside 1-{total}", hint=hint, pages=total)
    return a, b


def heading_span(view: DocView, heading: str) -> tuple[int, int, str]:
    items, _ = outline(view, limit=10_000)
    want = heading.strip().lower()
    paged = [i for i in items if i.page is not None]
    match = next((i for i in paged if i.title.lower() == want), None) or next((i for i in paged if want in i.title.lower()), None)
    if match is None:
        titles = list(dict.fromkeys(i.title for i in items))
        by_low = {t.lower(): t for t in reversed(titles)}
        closest = [by_low[t] for t in difflib.get_close_matches(want, list(by_low), n=3, cutoff=0)]
        raise ToolFailure("heading_not_found", f"no heading matches {heading!r}", hint="pick one of `closest` or use pages",
                          closest=closest)
    idx = paged.index(match)
    end = view.pages or match.page
    for later in paged[idx + 1:]:
        if later.level <= match.level and later.page is not None:
            end = max(match.page, later.page - 1) if later.page > match.page else match.page
            break
    return match.page, end, match.title


def chunk_markdown_text(chunk, previous=None) -> str:
    """A chunk as readable Markdown: chunk texts carry no heading lines (the path is separate), so the heading is
    re-emitted whenever the path changes (always for the first chunk of a read)."""
    path = list(chunk.heading_path or [])
    if path and (previous is None or list(previous.heading_path or []) != path):
        return f"{'#' * min(len(path), 6)} {path[-1]}\n\n{chunk.text}"
    return chunk.text


DEFAULT_CHUNK_TOKENS = 800
_CHUNK_ID = re.compile(r"^c(\d{1,6})(?:m(\d{3,4}))?$")


def chunk_uri_id(index: int, max_tokens: int = DEFAULT_CHUNK_TOKENS) -> str:
    """MCP chunk ids are `c0007` (`c0007m2000` for a non-default chunk size): `aidoc chunk` ids are `<stem>#0007`,
    and a `#` (or a CJK / spaced stem) cannot sit in a doc4ai://chunks/{doc_id}/{chunk_id} URI. The doc_id in the
    URI already scopes the index."""
    return f"c{index:04d}" + ("" if max_tokens == DEFAULT_CHUNK_TOKENS else f"m{max_tokens}")


def chunk_index(chunk_id: str) -> tuple[int, int] | None:
    m = _CHUNK_ID.match(chunk_id or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or DEFAULT_CHUNK_TOKENS)
