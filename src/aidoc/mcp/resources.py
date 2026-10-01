"""Resources and templates (MCP spec §6). Templates mirror what the tools return as URIs; `resources/list` shows
documents only. Errors become ResourceError (JSON-RPC -32602 per the SDK)."""
from __future__ import annotations

import functools
import json
import mimetypes
import os
from pathlib import Path

from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.mcpserver.exceptions import ResourceNotFoundError
from mcp.types import Resource

from aidoc.mcp import docs as D
from aidoc.mcp.errors import ToolFailure
from aidoc.output import read_sidecar
from aidoc.server.app import safe_relative

LIST_MAX = 100


def _guard(fn):
    @functools.wraps(fn)                            # the SDK matches template variables against the signature
    def run(*a, **kw):
        try:
            return fn(*a, **kw)
        except ToolFailure as f:                    # JSON-RPC -32602 (invalid params), never a 500-style -32603
            raise ResourceNotFoundError(f"{f.code}: {f.message}") from None
    return run


def register_resources(mcp, ctx) -> None:
    @mcp.resource("doc4ai://documents/{doc_id}", name="document", title="Document Markdown", mime_type="text/markdown",
                  description="The whole converted document (may be large; prefer read_document for slices)")
    @_guard
    def document(doc_id: str) -> str:
        view = D.load_doc(ctx, doc_id)
        return D.rewrite_assets(view.markdown, view.doc_id)

    @mcp.resource("doc4ai://documents/{doc_id}/pages/{range}", name="document_pages", title="Document pages",
                  mime_type="text/markdown", description="A page range such as 12 or 12-15")
    @_guard
    def document_pages(doc_id: str, range: str) -> str:
        view = D.load_doc(ctx, doc_id)
        if not view.has_pages:
            raise ToolFailure("page_range_invalid", "this document has no page markers", pages=None)
        a, b = D.parse_pages(range, view.pages)
        return D.rewrite_assets("".join(view.page_block(p) for p in range_(a, b)), view.doc_id)

    @mcp.resource("doc4ai://documents/{doc_id}/metadata", name="document_metadata", title="Document metadata",
                  mime_type="application/json")
    @_guard
    def document_metadata(doc_id: str) -> str:
        row = D.doc_row(ctx, doc_id)
        public = {k: v for k, v in row.items() if k not in ("work_copy_path", "work_copy_expires_at")}
        return json.dumps({"document": public, "sidecar": read_sidecar(Path(row["output_dir"]))}, ensure_ascii=False)

    @mcp.resource("doc4ai://documents/{doc_id}/assets/{name}", name="document_asset", title="Document image",
                  mime_type="application/octet-stream")
    @_guard
    def document_asset(doc_id: str, name: str) -> bytes:
        row = D.doc_row(ctx, doc_id)
        rel = safe_relative(name)
        if rel is None:
            raise ToolFailure("invalid_arguments", "bad asset name")
        base = (Path(row["output_dir"]) / "assets").resolve()
        target = (base / rel).resolve()
        if os.path.commonpath([str(base), str(target)]) != str(base) or not target.is_file():
            raise ToolFailure("document_not_found", f"no asset {name!r}")
        return target.read_bytes()

    @mcp.resource("doc4ai://chunks/{doc_id}/{chunk_id}", name="chunk", title="RAG chunk", mime_type="text/markdown")
    @_guard
    def chunk(doc_id: str, chunk_id: str) -> str:
        view = D.load_doc(ctx, doc_id)
        ref = D.chunk_index(chunk_id)
        if ref is not None and 200 <= ref[1] <= 2000:
            chunks = D.chunk_cache(view, ref[1])
            if ref[0] < len(chunks):
                return D.rewrite_assets(D.chunk_markdown_text(chunks[ref[0]]), view.doc_id)
        raise ToolFailure("document_not_found", f"no chunk {chunk_id!r} in this document", hint="chunk ids come from get_chunks")

    # resources/list: documents only (per spike S9 — pagination if the SDK forwards the cursor, else the newest 100)
    async def list_resources():
        docs = sorted(D.visible_docs(ctx.store), key=lambda d: -(d["created_at"] or 0))[:LIST_MAX]
        return [Resource(uri=f"doc4ai://documents/{d['id']}", name=Path(d["output_dir"]).name, mime_type="text/markdown",
                         description=f"{d.get('pages') or '?'} pages, {d['engine']}") for d in docs]
    mcp.list_resources = list_resources            # instance override; MCPServer._handle_list_resources calls it (S9)

    # a template has one fixed mimeType; an asset's real type comes from its file name
    original_read = mcp.read_resource

    async def read_resource(uri, context=None):
        results = await original_read(uri, context)
        if "/assets/" not in str(uri) or not isinstance(results, list | tuple):
            return results
        mime = mimetypes.guess_type(str(uri).rsplit("/", 1)[-1])[0] or "application/octet-stream"
        return [ReadResourceContents(content=r.content, mime_type=mime, meta=r.meta) for r in results]
    mcp.read_resource = read_resource


def range_(a: int, b: int):
    return range(a, b + 1)
