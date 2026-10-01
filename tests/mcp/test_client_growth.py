"""Item 8: observed clients cannot grow without bound, and stats never invent per-tool rows from junk names."""
import time

from aidoc.mcp.calllog import CLIENT_NAME_MAX, CLIENT_VERSION_MAX, CallRecorder


def test_client_name_and_version_are_capped(ctx):
    rec = CallRecorder(ctx)
    cid = rec.note_client(token_id="t", client_name="n" * 5000, client_version="v" * 5000, protocol_version="2026-07-28",
                          user_agent="u" * 5000, ip="::1")
    row = ctx.store.get_mcp_client(cid)
    assert len(row["client_name"]) == CLIENT_NAME_MAX == 100 and len(row["client_version"]) == CLIENT_VERSION_MAX == 50
    assert len(row["user_agent"]) <= 300


def test_idle_clients_are_pruned_by_maintenance(ctx):
    from aidoc.server.maintenance import Maintenance
    now = time.time()
    old, _ = ctx.store.upsert_mcp_client(token_id="t", client_name="old", client_version="1", protocol_version=None,
                                         user_agent=None, ip=None, now=now - 91 * 86400)
    fresh, _ = ctx.store.upsert_mcp_client(token_id="t", client_name="fresh", client_version="1", protocol_version=None,
                                           user_agent=None, ip=None, now=now - 10 * 86400)
    res = Maintenance(ctx).run_once()
    assert res["mcp_clients_pruned"] == 1
    assert ctx.store.get_mcp_client(old) is None and ctx.store.get_mcp_client(fresh) is not None


def test_stats_bucket_unknown_tool_names(client, ctx):
    now = time.time()
    for name in ("read_document", "evil_tool_1", "x" * 300, "another-junk"):
        ctx.store.insert_mcp_call(ts=now - 5, status="tool_error", method="tools/call", tool_name=name, duration_ms=1)
    tools = {t["tool"]: t["calls"] for t in client.get("/api/mcp/stats?window=24h").json()["tools"]}
    assert tools == {"read_document": 1, "(unknown)": 3}
