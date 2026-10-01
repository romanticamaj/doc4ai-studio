"""Read-only tools (MCP spec §5.3). Each body is a plain function; registry.doc4ai_tool adds scope checks."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from aidoc import pageindex
from aidoc.mcp import docs as D
from aidoc.mcp.budget import CursorError, decode_cursor, encode_cursor
from aidoc.mcp.errors import ToolFailure
from aidoc.mcp.principal import SCOPE_READ
from aidoc.mcp.registry import doc4ai_tool
from aidoc.mcp.schemas import (
    DocInfoOut,
    DocSummary,
    ListDocumentsOut,
    ListFilters,
    PageMapBrief,
    QualityBrief,
    QualityFull,
    SearchFilters,
    SearchHit,
    SearchOut,
)
from aidoc.reassess import doc_flags

MAX_PAGES_PER_DOC = 3


def _cursor(cursor: str | None, *keys: str) -> dict | None:
    try:
        d = decode_cursor(cursor)
    except CursorError:
        raise ToolFailure("invalid_cursor", "the cursor is not one this server issued", hint="start again without a cursor") from None
    if d is not None and set(d) != set(keys):
        raise ToolFailure("invalid_cursor", "the cursor belongs to another tool", hint="start again without a cursor")
    return d


def _score(rank) -> float:
    """bm25() is negative (lower = better); a hit always gets a positive score, even when the corpus is so small
    that the IDF term collapses to SQLite's 1e-6 floor (or the LIKE fallback reports rank 0)."""
    return max(round(-float(rank or 0.0), 6), 1e-6)


def _is_flagged(d: dict) -> bool:
    """Flagged = something on its pages needs attention (flagged pages, page-map problems). "unassessed" (not yet
    re-graded by the per-page check) is a maintenance state, not a quality finding."""
    return D.flagged_count(d) > 0 or bool(set(doc_flags(d)) - {"unassessed"})


def _filtered_docs(store, engine=None, level=None, flagged=None, q=None, doc_ids=None) -> list[dict]:
    out = []
    wanted = set(doc_ids) if doc_ids else None
    for d in D.visible_docs(store):
        if wanted is not None and d["id"] not in wanted:
            continue
        if engine and d["engine"] != engine:
            continue
        if level and d["status"] != level:
            continue
        if flagged is not None and _is_flagged(d) != flagged:
            continue
        if q and q.lower() not in (Path(d["output_dir"]).name + " " + str(d["source_path"])).lower():
            continue
        out.append(d)
    return out


def register_read_tools(mcp, ctx) -> None:
    store = ctx.store

    @doc4ai_tool(mcp, ctx, name="search_library", title="Search the library",
                 description="Full-text search over every page of every converted document. Returns the best-matching pages "
                             "(at most 3 per document; `more_in_doc` counts the rest) with a ~300-character snippet and a "
                             "resource URI. Use the doc_id and page with read_document to read the page.",
                 scope=SCOPE_READ, read_only=True, idempotent=True)
    def search_library(query: Annotated[str, Field(min_length=1, max_length=200)],
                       filters: SearchFilters | None = None,
                       limit: Annotated[int, Field(ge=1, le=20)] = 10,
                       cursor: str | None = None) -> SearchOut:
        cur = _cursor(cursor, "skip") or {"skip": 0}
        f = filters or SearchFilters()
        allowed = None
        if f.engine or f.level or f.flagged is not None or f.doc_ids:
            allowed = [d["id"] for d in _filtered_docs(store, f.engine, f.level, f.flagged, None, f.doc_ids)]
            if not allowed:
                return SearchOut(hits=[], next_cursor=None, query=query)
        else:
            allowed = [d["id"] for d in D.visible_docs(store)]
            if not allowed:                              # an empty id list would mean "no filter" to the store
                return SearchOut(hits=[], next_cursor=None, query=query)
        rows = pageindex.search_pages(store, query, doc_ids=allowed, limit=400)
        per_doc: dict[str, list[dict]] = {}
        for r in rows:
            per_doc.setdefault(r["doc_id"], []).append(r)
        hits: list[SearchHit] = []
        titles: dict[str, str] = {}
        for r in rows:                                   # rank order preserved; the first 3 pages of a doc are kept
            lst = per_doc[r["doc_id"]]
            if lst.index(r) >= MAX_PAGES_PER_DOC:
                continue
            if r["doc_id"] not in titles:
                row = store.get_document(r["doc_id"])
                titles[r["doc_id"]] = Path(row["output_dir"]).name if row else r["doc_id"]
            uri = f"doc4ai://documents/{r['doc_id']}" + (f"/pages/{r['page']}" if r["page"] is not None else "")
            hits.append(SearchHit(doc_id=r["doc_id"], title=titles[r["doc_id"]], page=r["page"],
                                  snippet=pageindex.make_snippet(r["text"], query), score=_score(r["rank"]),
                                  more_in_doc=max(0, len(lst) - MAX_PAGES_PER_DOC) if lst.index(r) == 0 else 0, uri=uri))
        skip = int(cur["skip"])
        page = hits[skip: skip + limit]
        nxt = encode_cursor({"skip": skip + limit}) if skip + limit < len(hits) else None
        return SearchOut(hits=page, next_cursor=nxt, query=query)

    @doc4ai_tool(mcp, ctx, name="list_documents", title="List documents",
                 description="Converted documents in the library with page count, engine and quality. Sorted by last "
                             "conversion (default) or title. Use get_document_info before reading a document.",
                 scope=SCOPE_READ, read_only=True, idempotent=True)
    def list_documents(cursor: str | None = None,
                       limit: Annotated[int, Field(ge=1, le=25)] = 25,
                       sort: Literal["updated_desc", "title_asc"] = "updated_desc",
                       filters: ListFilters | None = None) -> ListDocumentsOut:
        cur = _cursor(cursor, "k", "id")
        f = filters or ListFilters()
        docs = _filtered_docs(store, f.engine, f.level, f.flagged, f.q)
        if sort == "title_asc":
            docs.sort(key=lambda d: (Path(d["output_dir"]).name.lower(), d["id"]))
            key = lambda d: Path(d["output_dir"]).name.lower()
        else:
            docs.sort(key=lambda d: (-(d["created_at"] or 0), d["id"]))
            key = lambda d: d["created_at"]
        start = 0
        if cur is not None:
            start = next((i + 1 for i, d in enumerate(docs) if d["id"] == cur["id"]), None)
            if start is None:                          # the row vanished: resume after its sort key
                start = next((i for i, d in enumerate(docs) if (key(d), d["id"]) > (cur["k"], cur["id"])), len(docs)) \
                    if sort == "title_asc" else next((i for i, d in enumerate(docs) if key(d) < cur["k"]), len(docs))
        page = docs[start: start + limit]
        out = [DocSummary(doc_id=d["id"], title=Path(d["output_dir"]).name,
                          source_name=Path(str(d["source_path"]).replace("\\", "/")).name or Path(d["output_dir"]).name,
                          pages=d.get("pages"), engine=d["engine"],
                          quality=QualityBrief(level=(d.get("quality") or {}).get("level", d["status"]),
                                               score=float((d.get("quality") or {}).get("score", 0.0))),
                          flagged_pages=D.flagged_count(d), updated_at=float(d["created_at"] or 0)) for d in page]
        nxt = encode_cursor({"k": key(page[-1]), "id": page[-1]["id"]}) if page and start + limit < len(docs) else None
        return ListDocumentsOut(documents=out, next_cursor=nxt, total=len(docs))

    @doc4ai_tool(mcp, ctx, name="get_document_info", title="Get document info",
                 description="Metadata for one document: page count, quality level and flagged pages, outline (headings "
                             "with page numbers, max 200), token estimate per 20-page range, chunk count and resource URIs. "
                             "Call this before read_document to plan which pages to read.",
                 scope=SCOPE_READ, read_only=True, idempotent=True)
    def get_document_info(doc_id: Annotated[str, Field(min_length=1, max_length=64)]) -> DocInfoOut:
        view = D.load_doc(ctx, doc_id)
        row, q = view.row, view.row.get("quality") or {}
        items, trunc = D.outline(view)
        pm = q.get("page_map") or None
        page_map = None
        if pm:
            al = pm.get("alignment") or {}
            page_map = PageMapBrief(expected=pm.get("expected"), found=pm.get("found"), coverage=pm.get("coverage"),
                                    alignment=al.get("ratio") if isinstance(al, dict) else None)
        flagged = D.page_warnings(row)
        job = D.stale_job(ctx, row)
        return DocInfoOut(doc_id=view.doc_id, title=view.title, source_name=view.source_name, pages=view.pages,
                          engine=row["engine"], lang=row.get("lang") or "cht",
                          quality=QualityFull(level=q.get("level", row["status"]), score=float(q.get("score", 0.0)),
                                              reasons=list(q.get("reasons") or [])),
                          page_map=page_map, flagged_pages=flagged[:D.FLAGGED_MAX], flagged_pages_truncated=len(flagged) > D.FLAGGED_MAX,
                          outline=items, outline_truncated=trunc, token_estimate=D.token_ranges(view),
                          chunks=len(D.chunk_cache(view, 800)), resources=D.resources_for(view.doc_id),
                          stale=job is not None, job_id=job)
