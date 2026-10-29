"""MCP never returns the absolute source-copy path (spec 2026-10-29 §10.2; plan Task 8, A10)."""
from __future__ import annotations

import json
import shutil

import anyio

from tests.mcp.conftest import mcp_call, mcp_client


def _read(base, token, uri):
    async def go():
        async with mcp_client(base, token) as s:
            return await s.read_resource(uri)
    return anyio.run(go)


def _text(result) -> str:
    parts = [json.dumps(getattr(result, "structured_content", None) or {}, ensure_ascii=False)]
    for c in getattr(result, "content", None) or []:
        parts.append(getattr(c, "text", "") or "")
    return "\n".join(parts)


def test_no_absolute_source_copy_path_anywhere(mcp_server, mcp_env, tmp_root, fixtures):
    from aidoc.batch import register_source
    from aidoc.models import ConvertOptions
    ctx = mcp_env.ctx
    src = tmp_root / "Heading.pdf"
    shutil.copy(fixtures / "text.pdf", src)
    opts = ConvertOptions(output_dir=ctx.config.output_root())
    register_source(ctx.store, ctx.store.create_job(opts, "web"), src, opts)
    ctx.queue.process_next()
    doc = ctx.store.list_documents()[0]
    assert doc["source_copy_status"] == "copied" and doc["source_copy_path"]
    ctx.store.update_document(doc["id"], source_copy_error="io_error: something at " + doc["source_copy_path"])
    raw, _ = mcp_env.issue(scopes=("doc4ai:read",))
    root = str(ctx.config.source_root())
    meta = _read(mcp_server, raw, f"doc4ai://documents/{doc['id']}/metadata").contents[0].text
    texts = [meta, _text(mcp_call(mcp_server, raw, "get_document_info", {"doc_id": doc["id"]})),
             _text(mcp_call(mcp_server, raw, "list_documents", {})),
             _text(mcp_call(mcp_server, raw, "search_library", {"query": "Heading"}))]
    for t in texts:
        assert root not in t and root.replace("\\", "\\\\") not in t and root.replace("\\", "/") not in t
        assert "source_copy_path" not in t and "source_copy_error" not in t           # A10
    m = json.loads(meta)
    assert m["document"]["source_copy_status"] == "copied"
    assert '"source_copy_status": "copied"' in meta
    assert m["sidecar"]["source_copy"]["path"] == "source/Heading/Heading.pdf"           # relative: fine
