"""A PAT (or another bearer secret / a big base64 blob) passed anywhere — tool arguments, resource URIs, query
strings, error text, log lines — never reaches mcp_calls, the admin API, the SSE stream or the server log."""
import json
import logging

import anyio
import pytest

from aidoc.mcp import tokens as T
from aidoc.mcp.calllog import CallRecorder, summarize_args
from aidoc.mcp.redact import redact_text, redact_value
from tests.mcp.conftest import mcp_call, mcp_client


def _secret_body(tok: str) -> str:
    return tok[len(T.PREFIX):]


def test_redact_text_and_value():
    tok = T.generate_token()
    out = redact_text(f"look for {tok} and Bearer abcdefghijklmnopqrstuvwx and doc4ai_pat_SHORT")
    assert _secret_body(tok) not in out and f"doc4ai_pat_<redacted:{tok[11:15]}>" in out
    assert "abcdefghijklmnopqrstuvwx" not in out and "Bearer <redacted>" in out and "doc4ai_pat_SHORT" not in out
    v = redact_value({"query": tok, "nested": [{"p": "x" + tok}], "blob": "QUJD" * 100, "short": "hello", "n": 3})
    s = json.dumps(v)
    assert _secret_body(tok) not in s and v["n"] == 3 and v["short"] == "hello"
    assert v["blob"].startswith("<base64 len=400 sha256=") and v["blob"].endswith(">")


def test_summarize_args_redacts_every_key():
    tok = T.generate_token()
    s = summarize_args({"query": tok, "doc_id": tok, "filename": f"{tok}.pdf", "path": f"C:/x/{tok}", "data": "A" * 250})
    assert _secret_body(tok) not in s and "<base64 len=250" in s


def test_record_redacts_resource_uri_in_row_and_event(ctx):
    tok = T.generate_token()
    rid = CallRecorder(ctx).record(status="protocol_error", method="resources/read", resource_uri=f"doc4ai://documents/{tok}")
    row = ctx.store.get_mcp_call(rid)
    assert _secret_body(tok) not in row["resource_uri"] and "doc4ai_pat_<redacted:" in row["resource_uri"]
    ev = [e["payload"] for e in ctx.store.events_since(0) if e["kind"] == "mcp.call"][-1]
    assert _secret_body(tok) not in json.dumps(ev)


def test_secrets_in_live_calls_never_reach_log_api_or_events(mcp_server, mcp_env, client, caplog):
    from mcp.shared.exceptions import MCPError

    from aidoc.server.logfilter import install_secret_redaction
    install_secret_redaction()
    caplog.set_level(logging.INFO)
    raw, _ = mcp_env.issue()
    leaked = T.generate_token()                                       # a second token the AI pasted by mistake
    mcp_call(mcp_server, raw, "search_library", {"query": leaked})
    mcp_call(mcp_server, raw, "get_document_info", {"doc_id": leaked})

    async def read():
        async with mcp_client(mcp_server, raw) as s:
            await s.read_resource(f"doc4ai://documents/{leaked}")
    with pytest.raises(MCPError):
        anyio.run(read)
    body = _secret_body(leaked)
    rows = mcp_env.ctx.store.list_mcp_calls(limit=50)
    assert all(body not in json.dumps(r) for r in rows)
    assert body not in client.get("/api/mcp/calls?limit=100").text
    assert all(body not in json.dumps(e["payload"]) for e in mcp_env.ctx.store.events_since(0))
    assert all(body not in r.getMessage() for r in caplog.records)
    assert any("redacted" in r.getMessage() for r in caplog.records)     # the SDK did log the failing URI


def test_access_log_redacts_percent_encoded_keys_and_pats_anywhere():
    from aidoc.server.logfilter import redact
    tok = T.generate_token()
    assert "abc" not in redact('GET /api/events?%74oken=abc HTTP/1.1')
    assert "abc" not in redact('GET /api/events?x=1&ACCESS%5Ftoken=abc HTTP/1.1')
    assert _secret_body(tok) not in redact(f"GET /mcp/{tok}?q={tok.replace('_', '%5F')} HTTP/1.1")
