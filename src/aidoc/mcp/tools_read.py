"""Read-only tools (MCP spec §5.3). Each body is a plain function; registry.doc4ai_tool adds scope checks."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from aidoc import pageindex
from aidoc.mcp import docs as D
from aidoc.mcp.budget import CursorError, cut_to_budget, decode_cursor, encode_cursor, estimate_tokens
from aidoc.mcp.errors import ToolFailure
from aidoc.mcp.principal import SCOPE_READ
from aidoc.mcp.registry import TEXT_RESERVE_TOKENS, doc4ai_tool, render_text
from aidoc.mcp.schemas import (
    ChunkOut,
    ChunksOut,
    DocInfoOut,
    DocSummary,
    JobOut,
    ListDocumentsOut,
    ListFilters,
    NextRef,
    PageMapBrief,
    PageSpan,
    Progress,
    QualityBrief,
    QualityFull,
    ReadOut,
    SearchFilters,
    SearchHit,
    SearchOut,
    TaskBrief,
)
from aidoc.reassess import doc_flags
from aidoc.server.serialize import serialize_task

MAX_PAGES_PER_DOC = 3


def fingerprint(*parts) -> str:
    """Ties a cursor to the query/sort/filters it was issued for: reusing it with others is refused."""
    raw = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def _bad_cursor(msg: str) -> ToolFailure:
    return ToolFailure("invalid_cursor", msg, hint="start again without a cursor")


def _cursor(cursor: str | None, spec: dict, fp: str) -> dict | None:
    """Decode a cursor and check it: exactly the keys in `spec` plus the fingerprint, each of the expected type
    (ints never negative)."""
    try:
        d = decode_cursor(cursor)
    except CursorError:
        raise _bad_cursor("the cursor is not one this server issued") from None
    if d is None:
        return None
    if set(d) != set(spec) | {"f"}:
        raise _bad_cursor("the cursor belongs to another tool")
    if d["f"] != fp:
        raise _bad_cursor("the cursor was issued for a different query, sort or filters")
    for k, typ in spec.items():
        v = d[k]
        if isinstance(v, bool) or not isinstance(v, typ) or (isinstance(v, int) and v < 0):
            raise _bad_cursor(f"the cursor's {k!r} is malformed")
    return d


def _next(fp: str, **values) -> str:
    return encode_cursor({**values, "f": fp})


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


def _fit_info(out: DocInfoOut, budget: int) -> DocInfoOut:
    """Spec §1: every response ≤ the token budget. A long outline and a badly flagged scan can each be 200 entries;
    halve the longer list (marking it truncated) until the text rendering fits."""
    while estimate_tokens(render_text(out))[0] > budget and (len(out.outline) > 1 or len(out.flagged_pages) > 1):
        if len(out.outline) >= len(out.flagged_pages):
            out.outline, out.outline_truncated = out.outline[: len(out.outline) // 2], True
        else:
            out.flagged_pages, out.flagged_pages_truncated = out.flagged_pages[: len(out.flagged_pages) // 2], True
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
        f = filters or SearchFilters()
        fp = fingerprint("search_library", query, f.model_dump())
        cur = _cursor(cursor, {"skip": int}, fp) or {"skip": 0}
        allowed = None
        if f.engine or f.level or f.flagged is not None or f.doc_ids:
            allowed = [d["id"] for d in _filtered_docs(store, f.engine, f.level, f.flagged, None, f.doc_ids)]
            if not allowed:
                return SearchOut(hits=[], next_cursor=None, query=query)
        else:
            allowed = [d["id"] for d in D.visible_docs(store)]
            if not allowed:                              # an empty id list would mean "no filter" to the store
                return SearchOut(hits=[], next_cursor=None, query=query)
        try:
            rows, capped = pageindex.search_hits(store, query, doc_ids=allowed, per_doc=MAX_PAGES_PER_DOC)
        except sqlite3.Error as e:                      # never an UnexpectedToolError (review focus 2)
            raise ToolFailure("search_unavailable", f"the search index could not run this query ({e})",
                              hint="rephrase the query with plain words") from None
        hits: list[SearchHit] = []
        titles: dict[str, str] = {}
        seen: set[str] = set()
        for r in rows:                                   # at most 3 pages per document, already in result order
            if r["doc_id"] not in titles:
                row = store.get_document(r["doc_id"])
                titles[r["doc_id"]] = Path(row["output_dir"]).name if row else r["doc_id"]
            first = r["doc_id"] not in seen
            seen.add(r["doc_id"])
            uri = f"doc4ai://documents/{r['doc_id']}" + (f"/pages/{r['page']}" if r["page"] is not None else "")
            hits.append(SearchHit(doc_id=r["doc_id"], title=titles[r["doc_id"]], page=r["page"],
                                  snippet=pageindex.make_snippet(r["text"], query), score=_score(r["rank"]),
                                  more_in_doc=max(0, int(r["doc_matches"]) - MAX_PAGES_PER_DOC) if first else 0, uri=uri))
        skip = int(cur["skip"])
        page = hits[skip: skip + limit]
        nxt = _next(fp, skip=skip + limit) if skip + limit < len(hits) else None
        hint = ("more pages match than one search scans; narrow the query (more words, filters.doc_ids)"
                if capped else None)
        return SearchOut(hits=page, next_cursor=nxt, query=query, truncated=capped, hint=hint)

    @doc4ai_tool(mcp, ctx, name="list_documents", title="List documents",
                 description="Converted documents in the library with page count, engine and quality. Sorted by last "
                             "conversion (default) or title. Use get_document_info before reading a document.",
                 scope=SCOPE_READ, read_only=True, idempotent=True)
    def list_documents(cursor: str | None = None,
                       limit: Annotated[int, Field(ge=1, le=25)] = 25,
                       sort: Literal["updated_desc", "title_asc"] = "updated_desc",
                       filters: ListFilters | None = None) -> ListDocumentsOut:
        f = filters or ListFilters()
        fp = fingerprint("list_documents", sort, f.model_dump())
        cur = _cursor(cursor, {"k": str if sort == "title_asc" else (int, float), "id": str}, fp)
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
        nxt = _next(fp, k=key(page[-1]), id=page[-1]["id"]) if page and start + limit < len(docs) else None
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
        out = DocInfoOut(doc_id=view.doc_id, title=view.title, source_name=view.source_name, pages=view.pages,
                         engine=row["engine"], lang=row.get("lang") or "cht",
                         quality=QualityFull(level=q.get("level", row["status"]), score=float(q.get("score", 0.0)),
                                             reasons=list(q.get("reasons") or [])),
                         page_map=page_map, flagged_pages=flagged[:D.FLAGGED_MAX],
                         flagged_pages_truncated=len(flagged) > D.FLAGGED_MAX,
                         outline=items, outline_truncated=trunc, token_estimate=D.token_ranges(view),
                         chunks=len(D.chunk_cache(view, 800)), resources=D.resources_for(view.doc_id),
                         stale=job is not None, job_id=job)
        return _fit_info(out, ctx.config.mcp.response_token_budget)

    @doc4ai_tool(mcp, ctx, name="read_document", title="Read document pages",
                 description="Markdown of a page range (\"12\" or \"12-15\") or of the section under a heading. Responses are "
                             "capped (default 6,000 tokens, max 8,000): when `truncated` is true, call again with `next.pages` "
                             "(and `next.offset` when one page had to be cut). Documents without page markers are read as "
                             "chunks: use `chunk` from `next.chunk` to continue. Images are given as doc4ai:// resource URIs. "
                             "Page markers <!-- page: N --> are kept so you can cite pages.",
                 scope=SCOPE_READ, read_only=True, idempotent=True)
    def read_document(doc_id: Annotated[str, Field(min_length=1, max_length=64)],
                      pages: Annotated[str, Field(max_length=20)] | None = None,
                      heading: Annotated[str, Field(max_length=200)] | None = None,
                      chunk: Annotated[int, Field(ge=0)] | None = None,
                      offset: Annotated[int, Field(ge=0)] | None = None,
                      max_tokens: Annotated[int, Field(ge=500, le=8000)] = 6000) -> ReadOut:
        view = D.load_doc(ctx, doc_id)
        budget = max(200, min(max_tokens, ctx.config.mcp.response_token_budget) - TEXT_RESERVE_TOKENS)   # room for the header
        if pages is not None and heading is not None:
            raise ToolFailure("invalid_arguments", "give either pages or heading, not both")
        if chunk is not None and view.has_pages:              # never silently ignored
            raise ToolFailure("invalid_arguments", "chunk applies only to documents without page markers",
                              hint="this document has pages: read it with pages (and offset from next.offset)")
        job = D.stale_job(ctx, view.row)

        limit = min(max_tokens, ctx.config.mcp.response_token_budget)

        def build(budget: int) -> ReadOut:
            def collect(units: list, first_offset: int) -> tuple[list[str], int, int | None]:
                """Whole units while they fit; a first unit bigger than the budget is sliced. Units are counted after the
                asset URIs are rewritten (review: `](assets/x)` grows by ~45 characters each), and `offset` is a position in
                that rewritten text. Returns (parts, units fully or partly read, offset to continue the next unit at)."""
                parts, used = [], 0
                for k, text in enumerate(units):
                    if k == 0 and first_offset:
                        if first_offset >= len(text):
                            raise ToolFailure("invalid_arguments", f"offset {first_offset} is past the end of this unit "
                                              f"({len(text)} characters)", hint="use the offset from next.offset")
                        text = text[first_offset:]
                    n = estimate_tokens(text)[0]
                    if used + n <= budget:
                        parts.append(text)
                        used += n
                        continue
                    if not parts:                                  # one unit bigger than the budget: slice it
                        kept, cut = cut_to_budget(text, budget)
                        return [kept], 1, (first_offset if k == 0 else 0) + (cut or 0)
                    return parts, k, None
                return parts, len(units), None

            if not view.has_pages:                                 # chunk mode (spec §5.3, §9)
                if pages is not None or heading is not None:
                    raise ToolFailure("page_range_invalid", "this document has no page markers; read it by chunk",
                                      hint="omit pages/heading and follow next.chunk", pages=None)
                chunks = D.chunk_cache(view, 800)
                start = chunk or 0
                if start >= len(chunks):
                    raise ToolFailure("invalid_arguments", f"chunk {start} is past the end ({len(chunks)} chunks)")
                units = [D.rewrite_assets(D.chunk_markdown_text(chunks[i], None if i == start else chunks[i - 1]), view.doc_id)
                         for i in range(start, len(chunks))]
                parts, taken, cut_at = collect(units, offset or 0)
                nxt = None
                if cut_at is not None:
                    nxt = NextRef(chunk=start + taken - 1, offset=cut_at)
                elif start + taken < len(chunks):
                    nxt = NextRef(chunk=start + taken)
                md = "\n\n".join(parts)
                return ReadOut(doc_id=view.doc_id, title=view.title, unit="chunks", pages=None, markdown=md, truncated=nxt is not None,
                               next=nxt, page_warnings=[], tokens_est=estimate_tokens(md)[0], stale=job is not None, job_id=job)
            total = view.pages
            if heading is not None:
                start, end, _ = D.heading_span(view, heading)
            elif pages is not None:
                start, end = D.parse_pages(pages, total)
            else:
                start, end = 1, total
            units = [D.rewrite_assets(view.page_block(p), view.doc_id) for p in range(start, end + 1)]
            parts, taken, cut_at = collect(units, offset or 0)
            last = start + taken - 1
            nxt = None
            if cut_at is not None:                                 # the page `last` was cut: continue inside it
                nxt = NextRef(pages=f"{last}-{end}" if end > last else str(last), offset=cut_at)
            elif last < end:
                nxt = NextRef(pages=f"{last + 1}-{end}" if end > last + 1 else str(end))
            md = "".join(parts)
            return ReadOut(doc_id=view.doc_id, title=view.title, unit="pages", pages=PageSpan(start=start, end=last),
                           markdown=md, truncated=nxt is not None, next=nxt, page_warnings=D.page_warnings(view.row, start, last),
                           tokens_est=estimate_tokens(md)[0], stale=job is not None, job_id=job)

        # Item 5 (final review): a client may show the model either the text block or structuredContent; both
        # must fit. JSON escaping can make the structured copy much larger than the Markdown, so cut again with
        # a smaller budget until the bigger of the two representations fits (a few rounds at most).
        out = build(budget)
        for _ in range(6):
            biggest = max(estimate_tokens(render_text(out))[0],
                          estimate_tokens(json.dumps(out.model_dump(mode="json"), ensure_ascii=False,
                                                     separators=(",", ":")))[0])
            if biggest <= limit or budget <= 200:
                break
            budget = max(200, budget - (biggest - limit) - 50)
            out = build(budget)
        return out

    @doc4ai_tool(mcp, ctx, name="get_chunks", title="Get RAG chunks",
                 description="Heading-aware chunks of one document (same algorithm as `aidoc chunk`), each with its heading "
                             "path and page range. Paginated; the page is also cut to the response budget.",
                 scope=SCOPE_READ, read_only=True, idempotent=True)
    def get_chunks(doc_id: Annotated[str, Field(min_length=1, max_length=64)],
                   max_tokens: Annotated[int, Field(ge=200, le=2000)] = 800,
                   cursor: str | None = None,
                   limit: Annotated[int, Field(ge=1, le=20)] = 10) -> ChunksOut:
        view = D.load_doc(ctx, doc_id)
        fp = fingerprint("get_chunks", doc_id, max_tokens)
        cur = _cursor(cursor, {"i": int}, fp) or {"i": 0}
        chunks = D.chunk_cache(view, max_tokens)
        start = int(cur["i"])
        budget = max(200, ctx.config.mcp.response_token_budget - TEXT_RESERVE_TOKENS)
        out, used = [], 0
        for c in chunks[start: start + limit]:
            n = estimate_tokens(c.text)[0] + 40              # + the per-chunk header line of the text rendering
            if out and used + n > budget:
                break
            out.append(ChunkOut(chunk_id=D.chunk_uri_id(start + len(out), max_tokens), heading_path=list(c.heading_path), page_start=c.page_start,
                                page_end=c.page_end, text=D.rewrite_assets(c.text, view.doc_id)))
            used += n
        nxt = _next(fp, i=start + len(out)) if start + len(out) < len(chunks) else None
        return ChunksOut(chunks=out, next_cursor=nxt, total=len(chunks))

    @doc4ai_tool(mcp, ctx, name="get_job", title="Get conversion job",
                 description="Status and progress of a conversion job started by convert_document / convert_path "
                             "(or from the web UI), with the resulting doc_id per file once finished.",
                 scope=SCOPE_READ, read_only=True)
    def get_job(job_id: Annotated[str, Field(min_length=1, max_length=64)]) -> JobOut:
        job = store.get_job(job_id)
        if job is None:
            raise ToolFailure("job_not_found", f"no job {job_id!r}", hint="job ids come from convert_document / convert_path")
        tasks, done, total, first_error = [], 0, 0, None
        for t in store.list_tasks(job_id):
            st = serialize_task(store, t, with_segments=False)
            done += st["progress"]["pages_done"]
            total += st["progress"]["pages_total"] or 0
            q = t.get("quality") or {}
            if t.get("error_msg") and first_error is None:
                first_error = f"{t.get('error_kind')}: {t['error_msg']}"
            tasks.append(TaskBrief(task_id=t["id"], source_name=Path(str(t["source_path"]).replace("\\", "/")).name,
                                   status=t["status"], engine=t.get("engine"), quality_level=q.get("level"),
                                   doc_id=st.get("document_id"), error=t.get("error_msg")))
        return JobOut(job_id=job_id, status=job["status"], origin=job["origin"], progress=Progress(pages_done=done, pages_total=total),
                      tasks=tasks, error=first_error)
