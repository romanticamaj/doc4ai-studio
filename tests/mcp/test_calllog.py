import json

from aidoc.mcp.calllog import ARGS_MAX, CallRecorder, summarize_args


def _events(ctx, kind):
    return [e for e in ctx.store.events_since(0) if e["kind"] == kind]


def test_summarize_args_redacts_base64_and_truncates():
    s = summarize_args({"filename": "a.pdf", "content_base64": "QUJD" * 1000, "engine": None})
    d = json.loads(s)
    assert d["filename"] == "a.pdf" and d["engine"] is None
    assert d["content_base64"] == {"len": 4000, "sha256_8": d["content_base64"]["sha256_8"]} and len(d["content_base64"]["sha256_8"]) == 8
    assert "QUJD" not in s
    long = summarize_args({"query": "x" * 2000})
    assert len(long) <= ARGS_MAX and long.endswith("…")
    assert summarize_args(None) is None and summarize_args({}) == "{}"


def test_record_inserts_and_publishes(ctx):
    rec = CallRecorder(ctx)
    tid = ctx.store.create_api_token(name="laptop", prefix="doc4ai_pat_AAAA", token_hash="h", scopes=["doc4ai:read"],
                                     expires_at=None)
    rid = rec.record(status="ok", token_id=tid, method="tools/call", tool_name="read_document",
                     args={"doc_id": "d", "pages": "1-3"}, duration_ms=12, http_status=200, response_bytes=900,
                     response_tokens_est=250, ip="100.64.0.9", protocol_version="2026-07-28")
    row = ctx.store.list_mcp_calls(limit=1)[0]
    assert row["id"] == rid and row["args_summary"] == '{"doc_id": "d", "pages": "1-3"}' and row["status"] == "ok"
    ev = _events(ctx, "mcp.call")
    assert len(ev) == 1
    p = ev[0]["payload"]
    assert p["id"] == rid and p["token_name"] == "laptop" and p["tool_name"] == "read_document" and p["status"] == "ok"
    assert "args_summary" not in p and "token_hash" not in json.dumps(p)


def test_record_auth_failure_without_token(ctx):
    rec = CallRecorder(ctx)
    rec.record(status="auth_error", token_prefix_seen="doc4ai_pat_3kX9", error_code="revoked", http_status=401, ip="::1")
    row = ctx.store.list_mcp_calls(limit=1)[0]
    assert row["token_id"] is None and row["token_prefix_seen"] == "doc4ai_pat_3kX9" and row["error_code"] == "revoked"
    assert _events(ctx, "mcp.call")[0]["payload"]["token_name"] is None


def test_note_client_states(ctx):
    rec = CallRecorder(ctx)
    tid = ctx.store.create_api_token(name="t", prefix="p", token_hash="h", scopes=["doc4ai:read"], expires_at=None)
    cid = rec.note_client(token_id=tid, client_name="claude-code", client_version="2.3.1", protocol_version="2026-07-28",
                          user_agent="ua", ip="1.1.1.1", now=1000.0)
    assert _events(ctx, "mcp.client")[-1]["payload"]["state"] == "new"
    assert rec.note_client(token_id=tid, client_name="claude-code", client_version="2.3.1", protocol_version="2026-07-28",
                           user_agent="ua", ip="1.1.1.1", now=1010.0) == cid
    assert len(_events(ctx, "mcp.client")) == 1                       # still active: no event
    assert rec.sweep_idle(now=1000.0 + 301) == []                      # last seen at 1010: 291 s ago, still active
    assert rec.sweep_idle(now=1010.0 + 301) == [cid]
    assert _events(ctx, "mcp.client")[-1]["payload"]["state"] == "idle"
    assert rec.sweep_idle(now=1010.0 + 302) == []                     # reported once
    rec.note_client(token_id=tid, client_name="claude-code", client_version="2.3.1", protocol_version="2026-07-28",
                    user_agent="ua", ip="1.1.1.1", now=1000.0 + 400)
    assert _events(ctx, "mcp.client")[-1]["payload"]["state"] == "active"
    p = _events(ctx, "mcp.client")[-1]["payload"]
    assert p["token_name"] == "t" and p["request_count"] == 3 and p["client_name"] == "claude-code"


def test_touch_token_is_throttled(ctx):
    rec = CallRecorder(ctx)
    tid = ctx.store.create_api_token(name="t", prefix="p", token_hash="h", scopes=["doc4ai:read"], expires_at=None)
    assert rec.touch_token(tid, "1.1.1.1", "claude-code/2.3.1", now=100.0) is True
    assert rec.touch_token(tid, "2.2.2.2", "cursor", now=105.0) is False
    row = ctx.store.get_api_token(tid)
    assert row["last_used_at"] == 100.0 and row["last_used_ip"] == "1.1.1.1" and row["last_client"] == "claude-code/2.3.1"
    assert rec.touch_token(tid, "2.2.2.2", "cursor", now=111.0) is True
    assert ctx.store.get_api_token(tid)["last_client"] == "cursor"


def test_start_stop_watcher(ctx):
    rec = CallRecorder(ctx)
    rec.start()
    assert rec._thread is not None and rec._thread.is_alive()
    rec.stop()
    assert not rec._thread.is_alive()


def test_legacy_identity_is_remembered_per_token_and_user_agent(ctx):
    """Spike S5: a 2025-era stateless client sends clientInfo only on `initialize`; later messages of the same token
    and User-Agent are attributed to it for an hour."""
    rec = CallRecorder(ctx)
    rec.remember_identity("tok", "cursor/1.0", "cursor", "1.2", now=1000.0)
    assert rec.recall_identity("tok", "cursor/1.0", now=1500.0) == ("cursor", "1.2")
    assert rec.recall_identity("tok", "other-ua", now=1500.0) == (None, None)
    assert rec.recall_identity("other", "cursor/1.0", now=1500.0) == (None, None)
    assert rec.recall_identity("tok", "cursor/1.0", now=1000.0 + 3601) == (None, None)
