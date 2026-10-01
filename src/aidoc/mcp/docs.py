"""Document access shared by tools and resources: a small cache of parsed Markdown, asset URI rewriting, page
warnings (spec 2026-10-01 per-page quality) and the "stale while reconverting" check (MCP spec §9)."""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from aidoc.mcp.errors import ToolFailure
from aidoc.mcp.schemas import PageWarning
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
