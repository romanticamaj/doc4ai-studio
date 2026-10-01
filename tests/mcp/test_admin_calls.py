import time

from aidoc.server.api.mcp import percentile


def _token(client, name="t"):
    return client.post("/api/mcp/tokens", json={"name": name, "scopes": ["doc4ai:read"]}).json()["record"]["id"]


def test_clients_listing(client, ctx):
    tid = _token(client)
    now = time.time()
    cid, _ = ctx.store.upsert_mcp_client(token_id=tid, client_name="claude-code", client_version="2.3", protocol_version="2026-07-28",
                                         user_agent="ua", ip="100.64.0.9", now=now)
    old, _ = ctx.store.upsert_mcp_client(token_id=tid, client_name="cursor", client_version="1", protocol_version="2025-11-25",
                                         user_agent=None, ip="10.0.0.2", now=now - 3600)
    all_ = client.get("/api/mcp/clients").json()["clients"]
    assert [c["id"] for c in all_] == [cid, old] and all_[0]["token_name"] == "t" and all_[0]["active"] is True and all_[1]["active"] is False
    assert all_[0]["protocol_version"] == "2026-07-28" and all_[0]["last_ip"] == "100.64.0.9" and all_[0]["request_count"] == 1
    assert [c["id"] for c in client.get("/api/mcp/clients?active=1").json()["clients"]] == [cid]
    assert client.get(f"/api/mcp/clients?token_id={tid}").json()["clients"][0]["id"] == cid
    assert client.get("/api/mcp/clients?token_id=nope").json()["clients"] == []


def test_calls_filters_and_cursor(client, ctx):
    tid = _token(client)
    cid, _ = ctx.store.upsert_mcp_client(token_id=tid, client_name="cc", client_version="1", protocol_version="2026-07-28",
                                         user_agent=None, ip="::1", now=time.time())
    ids = [ctx.store.insert_mcp_call(ts=1000.0 + i, status="ok" if i % 4 else "tool_error", token_id=tid if i % 2 else None,
                                     client_id=cid if i % 2 else None, method="tools/call",
                                     tool_name="read_document" if i < 8 else "search_library", duration_ms=10 * i, args_summary="{}")
           for i in range(12)]
    page = client.get("/api/mcp/calls?limit=5").json()
    assert [c["id"] for c in page["calls"]] == ids[-1:-6:-1] and page["next_cursor"] == str(ids[-5])
    assert page["calls"][0]["token_name"] == "t" and page["calls"][0]["client_name"] == "cc" and page["calls"][0]["args_summary"] == "{}"
    page2 = client.get(f"/api/mcp/calls?limit=5&cursor={page['next_cursor']}").json()
    assert [c["id"] for c in page2["calls"]] == ids[-6:-11:-1]
    last = client.get(f"/api/mcp/calls?limit=5&cursor={page2['next_cursor']}").json()
    assert len(last["calls"]) == 2 and last["next_cursor"] is None
    assert len(client.get(f"/api/mcp/calls?token_id={tid}&limit=100").json()["calls"]) == 6
    assert len(client.get(f"/api/mcp/calls?client_id={cid}&limit=100").json()["calls"]) == 6
    assert len(client.get("/api/mcp/calls?tool=search_library&limit=100").json()["calls"]) == 4
    assert len(client.get("/api/mcp/calls?status=tool_error&limit=100").json()["calls"]) == 3
    assert len(client.get("/api/mcp/calls?since=1004&until=1006&limit=100").json()["calls"]) == 3
    assert client.get("/api/mcp/calls?cursor=abc").status_code == 422
    assert client.get("/api/mcp/calls?cursor=abc").json()["error"] == "invalid_cursor"
    assert client.get("/api/mcp/calls?limit=0").status_code == 422 and client.get("/api/mcp/calls?limit=101").status_code == 422


def test_percentile():
    assert percentile([], 0.5) is None
    assert percentile([10], 0.95) == 10
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.5) == 5 and percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95) == 10


def test_stats_windows(client, ctx):
    tid = _token(client, "alpha")
    now = time.time()
    for i in range(10):
        ctx.store.insert_mcp_call(ts=now - 60 * i, status="ok" if i else "tool_error", token_id=tid, method="tools/call",
                                  tool_name="read_document", duration_ms=100 + i, response_tokens_est=1000 + 10 * i)
    ctx.store.insert_mcp_call(ts=now - 3 * 86400, status="ok", token_id=tid, method="tools/call", tool_name="read_document", duration_ms=5)
    ctx.store.insert_mcp_call(ts=now - 30, status="ok", token_id=tid, method="tools/list")        # not a tool
    s = client.get("/api/mcp/stats?window=24h").json()
    assert s["window"] == "24h" and now - s["since"] <= 86400 + 5 and len(s["series"]) == 24
    tool = next(t for t in s["tools"] if t["tool"] == "read_document")
    assert tool["calls"] == 10 and tool["errors"] == 1 and tool["error_rate"] == 0.1
    assert tool["p50_ms"] in (104, 105) and tool["p95_ms"] in (108, 109) and tool["tokens_median"] in (1040, 1050)
    assert s["tokens"] == [{"token_id": tid, "name": "alpha", "calls": 11, "errors": 1}]
    assert sum(b["calls"] for b in s["series"]) == 11 and sum(b["errors"] for b in s["series"]) == 1
    w = client.get("/api/mcp/stats?window=7d").json()
    assert len(w["series"]) == 28 and next(t for t in w["tools"] if t["tool"] == "read_document")["calls"] == 11
    assert client.get("/api/mcp/stats?window=1y").status_code == 422
