import json

import pytest

from aidoc.mcp.middleware import TOOL_SCOPES, visible_tools
from aidoc.mcp.principal import SCOPES, Principal
from aidoc.mcp.server import PROTOCOL_VERSIONS, endpoint_urls, local_hosts, transport_security_for
from tests.mcp.conftest import mcp_call, mcp_client, mcp_list_tools, raw_post

READ_TOOLS = {"search_library", "list_documents", "get_document_info", "read_document", "get_chunks", "get_job",
              "verify_document"}


@pytest.mark.parametrize("mode,version", [("auto", "2026-07-28"), ("legacy", "2025-11-25")])
def test_both_eras_list_tools(mcp_server, mcp_env, mode, version):
    raw, _ = mcp_env.issue()
    import anyio

    async def go():
        async with mcp_client(mcp_server, raw, mode) as s:
            tools = await s.list_tools()
            return s.protocol_version, [t.name for t in tools.tools], tools
    pv, names, res = anyio.run(go)
    assert pv == version
    assert set(names) == READ_TOOLS and names == sorted(names)                 # deterministic order
    if mode == "auto":
        assert res.ttl_ms == 300000                                            # per spike S10


def test_instructions_describe_the_flow(mcp_server, mcp_env):
    raw, _ = mcp_env.issue()
    import anyio

    async def go():
        async with mcp_client(mcp_server, raw, "legacy") as s:
            return s.instructions
    text = anyio.run(go)
    assert "search_library" in text and "get_document_info" in text and "read_document" in text and "8,000" in text


def test_tools_list_filtered_by_scope(mcp_server, mcp_env):
    raw, _ = mcp_env.issue(scopes=SCOPES)
    names = {t.name for t in mcp_list_tools(mcp_server, raw).tools}
    assert names == READ_TOOLS | {"convert_document", "verify_markdown", "cancel_job", "reconvert_document"}   # no roots:
    # convert_path hidden
    mcp_env.ctx.config.mcp.local_path_roots = [str(mcp_env.ctx.config.root)]
    names = {t.name for t in mcp_list_tools(mcp_server, raw).tools}
    assert "convert_path" in names
    mcp_env.ctx.config.mcp.local_path_roots = []
    raw2, _ = mcp_env.issue(scopes=("doc4ai:read", "doc4ai:convert"))
    assert {t.name for t in mcp_list_tools(mcp_server, raw2).tools} == READ_TOOLS | {"convert_document", "verify_markdown"}


def test_every_tool_has_title_annotations_and_output_schema(mcp_server, mcp_env):
    raw, _ = mcp_env.issue(scopes=SCOPES)
    mcp_env.ctx.config.mcp.local_path_roots = [str(mcp_env.ctx.config.root)]
    tools = mcp_list_tools(mcp_server, raw).tools
    mcp_env.ctx.config.mcp.local_path_roots = []
    assert {t.name for t in tools} == set(TOOL_SCOPES)
    for t in tools:
        assert t.title and t.description and t.output_schema and t.annotations is not None, t.name
        ro = t.name in READ_TOOLS
        assert t.annotations.read_only_hint is ro, t.name
        if t.name in ("cancel_job",):
            assert t.annotations.destructive_hint is True
        if t.name in ("convert_document", "convert_path", "reconvert_document"):
            assert t.annotations.destructive_hint is False
        assert t.annotations.open_world_hint is False


def test_forbidden_scope_hard_call_is_error_and_logged(mcp_server, mcp_env):
    raw, tid = mcp_env.issue()
    res = mcp_call(mcp_server, raw, "cancel_job", {"job_id": "x"})
    assert res.is_error is True
    payload = res.structured_content or json.loads(res.content[0].text)          # per spike S6
    err = payload.get("error", payload)                                          # per spike S7
    assert err["code"] == "forbidden_scope" and "doc4ai:manage" in err["message"]
    row = mcp_env.ctx.store.list_mcp_calls(limit=1)[0]
    assert row["status"] == "forbidden_scope" and row["tool_name"] == "cancel_job" and row["token_id"] == tid


def test_unknown_tool_is_protocol_error_logged(mcp_server, mcp_env):
    raw, _ = mcp_env.issue()
    r = raw_post(mcp_server, raw, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nope", "arguments": {}}})
    assert r.status_code in (200, 400, 404)
    row = mcp_env.ctx.store.list_mcp_calls(limit=1)[0]
    assert row["status"] in ("protocol_error", "tool_error") and row["tool_name"] == "nope"


def test_call_rows_carry_client_identity(mcp_server, mcp_env):
    from mcp.types import Implementation
    raw, tid = mcp_env.issue()
    for mode in ("auto", "legacy"):
        mcp_list_tools(mcp_server, raw, mode)
    rows = mcp_env.ctx.store.list_mcp_calls(limit=10)
    listed = [r for r in rows if r["method"] == "tools/list"]
    assert {r["protocol_version"] for r in listed} == {"2026-07-28", "2025-11-25"}
    assert all(r["token_id"] == tid and r["client_id"] and r["duration_ms"] is not None for r in listed)
    clients = mcp_env.ctx.store.list_mcp_clients(token_id=tid)
    assert clients and clients[0]["client_name"] == "doc4ai-tests"
    mcp_call(mcp_server, raw, "list_documents", {}, client_info=Implementation(name="other", version="2"))
    assert {c["client_name"] for c in mcp_env.ctx.store.list_mcp_clients(token_id=tid)} == {"doc4ai-tests", "other"}
    assert mcp_env.ctx.store.get_api_token(tid)["last_used_at"] is not None


def test_mcp_is_not_enveloped_or_compressed(mcp_server, mcp_env):
    raw, _ = mcp_env.issue()
    r = raw_post(mcp_server, raw, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                 extra_headers={"Accept-Encoding": "gzip, br"})
    assert r.status_code == 200
    assert "content-encoding" not in r.headers
    assert "workspace" not in r.text                                               # EnvelopeMiddleware passthrough


def test_rejected_host_is_421(mcp_server, mcp_env):
    raw, _ = mcp_env.issue()
    r = raw_post(mcp_server, raw, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                 extra_headers={"Host": "evil.example:8765"})
    assert r.status_code == 421
    r = raw_post(mcp_server, raw, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                 extra_headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_spa_catch_all_still_works(mcp_env):
    from fastapi.testclient import TestClient
    with TestClient(mcp_env.app, base_url="http://127.0.0.1:8765") as c:
        assert c.get("/mcp").status_code in (401, 405)         # the gate answers, not the SPA
        assert c.get("/library").status_code == 200             # SPA fallback (or the "not built" message)
        r = c.post("/mcp/", content=b"{}", follow_redirects=False)       # never redirected into /mcp
        assert r.status_code in (401, 404, 405) and "location" not in r.headers   # 405: the SPA route is GET/HEAD only


def test_transport_security_and_endpoint_urls(ctx):
    cfg = ctx.config
    ts = transport_security_for(cfg, "127.0.0.1", 8765)
    assert ts.enable_dns_rebinding_protection is True
    assert {"127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1:8765"} <= set(ts.allowed_hosts)
    assert "http://127.0.0.1:8765" in ts.allowed_origins and "http://localhost:8765" in ts.allowed_origins
    cfg.mcp.allowed_hosts = ["box.tail74077f.ts.net"]
    ts = transport_security_for(cfg, "0.0.0.0", 3333)
    assert "box.tail74077f.ts.net:*" in ts.allowed_hosts and "box.tail74077f.ts.net:3333" in ts.allowed_hosts
    assert any(h not in ("127.0.0.1:*", "localhost:*", "[::1]:*") for h in local_hosts("0.0.0.0"))   # interface IPs
    urls = endpoint_urls(cfg, "0.0.0.0", 3333)
    assert "http://box.tail74077f.ts.net:3333/mcp" in urls and all(u.endswith(":3333/mcp") for u in urls)
    assert endpoint_urls(cfg, "127.0.0.1", 8765) == ["http://127.0.0.1:8765/mcp"]
    assert PROTOCOL_VERSIONS == ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"]
    cfg.mcp.allowed_hosts = []


def test_visible_tools_rules(ctx):
    p = Principal(kind="pat", subject="owner", token_id="t", client_id=None, scopes=frozenset({"doc4ai:read", "doc4ai:convert:local"}),
                  expires_at=None)
    names = list(TOOL_SCOPES)
    assert set(visible_tools(names, p, ctx.config)) == READ_TOOLS                    # roots empty hides convert_path
    ctx.config.mcp.local_path_roots = ["C:/"]
    assert set(visible_tools(names, p, ctx.config)) == READ_TOOLS | {"convert_path"}
    ctx.config.mcp.local_path_roots = []
    assert visible_tools(names, None, ctx.config) == []


def test_tailnet_bind_advertises_magicdns_name(ctx, monkeypatch):
    """Bound to a tailnet IP, the MagicDNS name is an allowed Host and an advertised endpoint without any config."""
    from aidoc.mcp import server as srv
    monkeypatch.setattr(srv, "_tailnet_cache", {("100.106.118.45",): ["box.tail0.ts.net", "box"]})
    ts = transport_security_for(ctx.config, "100.106.118.45", 3333)
    assert {"box.tail0.ts.net:3333", "box.tail0.ts.net:*", "box:*", "100.106.118.45:3333"} <= set(ts.allowed_hosts)
    assert "http://box.tail0.ts.net:3333" in ts.allowed_origins
    assert endpoint_urls(ctx.config, "100.106.118.45", 3333) == ["http://100.106.118.45:3333/mcp",
                                                                  "http://box.tail0.ts.net:3333/mcp"]
    assert srv.tailnet_names([]) == [] and srv._is_tailnet_ip("100.64.0.1") and not srv._is_tailnet_ip("192.168.1.2")
