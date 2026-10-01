import json

import anyio
import pytest

from tests.mcp.conftest import mcp_client, page_md


def _read(base, token, uri, mode="auto"):
    async def go():
        async with mcp_client(base, token, mode) as s:
            return await s.read_resource(uri)
    return anyio.run(go)


def _list(base, token, mode="auto"):
    async def go():
        async with mcp_client(base, token, mode) as s:
            return await s.list_resources(), await s.list_resource_templates()
    return anyio.run(go)


def test_templates_and_document_listing(mcp_server, mcp_env, make_doc):
    a = make_doc("alpha", pages=2)
    make_doc("orph", pages=1, status="orphaned")
    raw, _ = mcp_env.issue()
    listed, templates = _list(mcp_server, raw)
    uris = {str(r.uri) for r in listed.resources}
    assert uris == {f"doc4ai://documents/{a['id']}"}
    assert listed.ttl_ms == 60000                                        # per spike S9/S10
    t = {str(x.uri_template) for x in templates.resource_templates}
    assert t == {"doc4ai://documents/{doc_id}", "doc4ai://documents/{doc_id}/pages/{range}", "doc4ai://documents/{doc_id}/metadata",
                 "doc4ai://documents/{doc_id}/assets/{name}", "doc4ai://chunks/{doc_id}/{chunk_id}"}


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_read_markdown_pages_metadata(mcp_server, mcp_env, make_doc, mode):
    doc = make_doc("r", pages=3, md="".join(page_md(n, body=f"![x](assets/p{n}_1.png) p{n}") for n in range(1, 4)))
    raw, _ = mcp_env.issue()
    whole = _read(mcp_server, raw, f"doc4ai://documents/{doc['id']}", mode).contents[0]
    assert whole.mime_type == "text/markdown" and "<!-- page: 3 -->" in whole.text and f"doc4ai://documents/{doc['id']}/assets/p1_1.png" in whole.text
    pages = _read(mcp_server, raw, f"doc4ai://documents/{doc['id']}/pages/2-3", mode).contents[0]
    assert pages.text.startswith("<!-- page: 2 -->") and "<!-- page: 1 -->" not in pages.text
    meta = json.loads(_read(mcp_server, raw, f"doc4ai://documents/{doc['id']}/metadata", mode).contents[0].text)
    assert meta["document"]["id"] == doc["id"] and "work_copy_path" not in meta["document"] and meta["sidecar"]["pages"] == 3


def test_read_asset_blob_and_chunk(mcp_server, mcp_env, make_doc):
    doc = make_doc("a", pages=1)
    raw, _ = mcp_env.issue()
    asset = _read(mcp_server, raw, f"doc4ai://documents/{doc['id']}/assets/p1_1.png").contents[0]
    assert asset.mime_type == "image/png" and asset.blob                 # base64 body
    from tests.mcp.conftest import mcp_call
    chunks = mcp_call(mcp_server, raw, "get_chunks", {"doc_id": doc["id"]}).structured_content["chunks"]
    c = _read(mcp_server, raw, f"doc4ai://chunks/{doc['id']}/{chunks[0]['chunk_id']}").contents[0]
    assert c.mime_type == "text/markdown" and c.text.strip()


@pytest.mark.parametrize("uri", ["doc4ai://documents/nope", "doc4ai://documents/{id}/pages/99", "doc4ai://documents/{id}/assets/../x.png",
                                 "doc4ai://documents/{id}/assets/missing.png", "doc4ai://chunks/{id}/zzz"])
def test_resource_errors_are_protocol_errors(mcp_server, mcp_env, make_doc, uri):
    from mcp.shared.exceptions import MCPError
    doc = make_doc("e", pages=1)
    raw, _ = mcp_env.issue()
    with pytest.raises(MCPError):
        _read(mcp_server, raw, uri.replace("{id}", doc["id"]))
    row = mcp_env.ctx.store.list_mcp_calls(limit=1)[0]
    assert row["method"] == "resources/read" and row["status"] == "protocol_error" and row["resource_uri"].startswith("doc4ai://")


def test_chunk_ids_are_uri_safe_and_resolve_per_chunk_size(mcp_server, mcp_env, make_doc):
    """`aidoc chunk` ids look like `stem#0003`; a `#` cannot sit in a URI, so MCP ids are `c0003` / `c0003m200`."""
    from tests.mcp.conftest import mcp_call
    md = "".join(page_md(n, heading=f"S{n}", body=" ".join(f"para {n}-{k} " * 20 for k in range(4))) for n in range(1, 4))
    doc = make_doc("中文 書名", pages=3, md=md)
    raw, _ = mcp_env.issue()
    small = mcp_call(mcp_server, raw, "get_chunks", {"doc_id": doc["id"], "max_tokens": 200}).structured_content["chunks"]
    cid = small[1]["chunk_id"]
    assert cid == "c0001m200"
    text = _read(mcp_server, raw, f"doc4ai://chunks/{doc['id']}/{cid}").contents[0].text
    assert small[1]["text"] in text


def test_resources_need_the_read_scope(mcp_server, mcp_env, make_doc):
    """Defense in depth (S-phase finding 3): the admin API never issues a token without doc4ai:read, but a token
    row without it (hand-made, future scope sets) must not read through resources either."""
    from mcp.shared.exceptions import MCPError
    doc = make_doc("scoped", pages=2)
    raw, _ = mcp_env.issue(scopes=("doc4ai:convert",))
    listed, templates = _list(mcp_server, raw)
    assert listed.resources == [] and templates.resource_templates == []
    for uri in (f"doc4ai://documents/{doc['id']}", f"doc4ai://documents/{doc['id']}/pages/1",
                f"doc4ai://documents/{doc['id']}/metadata", f"doc4ai://documents/{doc['id']}/assets/p1_1.png",
                f"doc4ai://chunks/{doc['id']}/c0000"):
        with pytest.raises(MCPError) as e:
            _read(mcp_server, raw, uri)
        assert "forbidden_scope" in str(e.value)
    rows = mcp_env.ctx.store.list_mcp_calls(limit=5)
    assert rows[0]["method"] == "resources/read" and rows[0]["status"] == "forbidden_scope"
    ok, _ = mcp_env.issue()
    assert _read(mcp_server, ok, f"doc4ai://documents/{doc['id']}/pages/1").contents[0].text.startswith("<!-- page: 1 -->")
