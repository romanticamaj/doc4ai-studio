import json
import logging
import sqlite3
import time

import httpx
from fastapi.testclient import TestClient

from aidoc.mcp.principal import SCOPES
from tests.mcp.conftest import mcp_call, mcp_list_tools, raw_post

LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def test_pat_rejected_on_api(token_ctx, mcp_env):
    """A PAT is never a server token: /api answers 401 to it even when it is valid on /mcp."""
    from aidoc.mcp import tokens as T
    from aidoc.mcp.tokens import load_or_create_secret
    from aidoc.server.app import create_app
    secret = load_or_create_secret(token_ctx.config.data_dir)
    raw = T.generate_token()
    token_ctx.store.create_api_token(name="t", prefix=T.display_prefix(raw), token_hash=T.token_hash(secret, raw),
                                     scopes=list(SCOPES), expires_at=None)
    with TestClient(create_app(token_ctx), base_url="http://192.168.1.20:8765") as c:
        assert c.get("/api/system", headers={"Authorization": f"Bearer {raw}"}).status_code == 401
        assert c.get("/api/mcp/tokens", headers={"Authorization": f"Bearer {raw}"}).status_code in (401, 404)
        assert c.get("/api/system", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_server_token_is_not_a_pat(mcp_server, token_ctx):
    """The admin token is useless on /mcp."""
    r = raw_post(mcp_server, "s3cret", LIST)
    assert r.status_code == 401 and r.json()["error"] == "invalid_token"


def test_mcp_requires_token_even_on_loopback(mcp_server, mcp_env):
    r = raw_post(mcp_server, None, LIST)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == 'Bearer realm="doc4ai", error="invalid_token"' or \
        r.headers["www-authenticate"].startswith('Bearer realm="doc4ai", error="invalid_token", error_description=')
    assert "resource_metadata" not in r.headers["www-authenticate"]


def test_query_token_rejected_on_mcp(mcp_server, mcp_env):
    raw, _ = mcp_env.issue()
    r = httpx.post(f"{mcp_server}/mcp?token={raw}", json=LIST, headers={"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/list",
                                                                     "Accept": "application/json, text/event-stream"})
    assert r.status_code == 400 and r.json()["error"] == "token_in_query"
    row = mcp_env.ctx.store.list_mcp_calls(limit=1)[0]
    assert row["status"] == "auth_error" and row["error_code"] == "token_in_query" and raw not in json.dumps(row)


def test_token_never_persisted_or_streamed(mcp_server, mcp_env, make_doc, caplog):
    caplog.set_level(logging.DEBUG)
    make_doc("d", pages=2)
    raw, _tid = mcp_env.issue(scopes=SCOPES)
    mcp_list_tools(mcp_server, raw)
    mcp_call(mcp_server, raw, "read_document", {"doc_id": "nope"})               # a tool error
    raw_post(mcp_server, raw[:-1] + "0", LIST)                                   # an auth failure with the prefix seen
    con = sqlite3.connect(mcp_env.ctx.store.db_path)
    dump = "\n".join(con.iterdump())
    con.close()
    assert raw not in dump and raw[11:40] not in dump                            # neither the token nor its body
    events = json.dumps([e for e in mcp_env.ctx.store.events_since(0)], default=str)
    assert raw not in events and raw[11:40] not in events
    assert raw not in caplog.text and raw[11:40] not in caplog.text
    rows = mcp_env.ctx.store.list_mcp_calls(limit=10)
    assert all((r["token_prefix_seen"] or "").startswith("doc4ai_pat_") or r["token_prefix_seen"] is None for r in rows)
    assert all(len(r["token_prefix_seen"] or "") <= 15 for r in rows)


def test_host_and_origin_checks_are_logged(mcp_server, mcp_env):
    raw, _ = mcp_env.issue()
    assert raw_post(mcp_server, raw, LIST, extra_headers={"Host": "evil.example:8765"}).status_code == 421
    row = mcp_env.ctx.store.list_mcp_calls(limit=1)[0]
    assert row["status"] == "protocol_error" and row["error_code"] == "http_421"
    assert raw_post(mcp_server, raw, LIST, extra_headers={"Origin": "http://evil.example"}).status_code == 403
    assert mcp_env.ctx.store.list_mcp_calls(limit=1)[0]["error_code"] == "http_403"
    ok_origin = mcp_server.replace("127.0.0.1", "127.0.0.1")
    assert raw_post(mcp_server, raw, LIST, extra_headers={"Origin": ok_origin}).status_code == 200


def test_rate_limit_429_with_retry_after_logged(mcp_server, mcp_env, make_doc):
    make_doc("d", pages=1)
    raw, tid = mcp_env.issue(rate=2)
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_documents", "arguments": {}}}
    assert raw_post(mcp_server, raw, call).status_code == 200
    assert raw_post(mcp_server, raw, call).status_code == 200
    r = raw_post(mcp_server, raw, call)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    row = mcp_env.ctx.store.list_mcp_calls(limit=1)[0]
    assert row["status"] == "rate_limited" and row["token_id"] == tid
    assert raw_post(mcp_server, raw, LIST).status_code == 200                   # tools/list never limited


def test_revoked_token_401_is_logged_and_published(mcp_server, mcp_env):
    raw, tid = mcp_env.issue()
    assert raw_post(mcp_server, raw, LIST).status_code == 200
    mcp_env.ctx.store.revoke_api_token(tid, "lost laptop", at=time.time())
    r = raw_post(mcp_server, raw, LIST)
    assert r.status_code == 401
    row = mcp_env.ctx.store.list_mcp_calls(limit=1)[0]
    assert row["status"] == "auth_error" and row["error_code"] == "revoked" and row["token_prefix_seen"] == raw[:15]
    ev = [e for e in mcp_env.ctx.store.events_since(0) if e["kind"] == "mcp.call"][-1]
    assert ev["payload"]["status"] == "auth_error" and ev["payload"]["error_code"] == "revoked"


def test_scope_filter_hides_and_hard_call_refuses(mcp_server, mcp_env):
    raw, _ = mcp_env.issue(scopes=("doc4ai:read",))
    names = {t.name for t in mcp_list_tools(mcp_server, raw).tools}
    assert not names & {"convert_document", "convert_path", "cancel_job", "reconvert_document"}
    res = mcp_call(mcp_server, raw, "reconvert_document", {"doc_id": "x"})
    assert res.is_error and (res.structured_content or json.loads(res.content[0].text))["code"] == "forbidden_scope"


def test_disabled_is_404(mcp_server, mcp_env):
    raw, _ = mcp_env.issue()
    mcp_env.ctx.config.mcp.enabled = False
    try:
        r = raw_post(mcp_server, raw, LIST)
        assert r.status_code == 404 and r.json() == {"error": "mcp_disabled"}
    finally:
        mcp_env.ctx.config.mcp.enabled = True
    assert raw_post(mcp_server, raw, LIST).status_code == 200


def test_sse_payloads_never_carry_tokens(mcp_server, mcp_env):
    raw, _tid = mcp_env.issue()
    raw_post(mcp_server, raw, LIST)
    with httpx.Client(base_url=mcp_server, timeout=5) as c, c.stream("GET", "/api/events", headers={"Last-Event-ID": "0"}) as r:
        text = ""
        for line in r.iter_lines():
            text += line + "\n"
            if "mcp.call" in text and text.count("\n\n") >= 1 and "data:" in text:
                break
    assert raw not in text and raw[11:40] not in text and "token_hash" not in text
