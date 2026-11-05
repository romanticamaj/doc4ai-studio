"""OAuth connections in the MCP admin data (spec 2026-11-05 §7.1, §7.2): one identity per grant across refreshes."""
import base64
import time

from aidoc.oauth.grants import GrantService
from tests.mcp.conftest import mcp_call, mcp_list_tools
from tests.oauth.conftest import mint_tokens


def _res(base):
    return base + "/mcp"


# ---- part 1 (Task 9): clients, calls, jobs, touch

def test_oauth_identity_in_clients_calls_and_touch(live_oauth, oauth_ctx):
    store = oauth_ctx.store
    m = mint_tokens(oauth_ctx, scopes=("doc4ai:read",), resource=_res(live_oauth))
    mcp_list_tools(live_oauth, m.access_token)
    clients = store.list_mcp_clients()
    assert len(clients) == 1 and clients[0]["token_id"] == m.grant_id and clients[0]["oauth_client_id"] == m.client_id
    g = store.get_oauth_grant(m.grant_id)
    assert g["last_used_at"] is not None and g["last_used_ip"] == "127.0.0.1" and g["last_client"]
    at = store.active_oauth_tokens(m.grant_id, "at", time.time())[0]
    assert at["last_used_at"] is not None
    ev = [e for e in store.events_since(0) if e["kind"] == "mcp.call"][-1]["payload"]
    assert ev["token_id"] == m.grant_id and ev["token_name"] == "Test IDE"
    cl = [e for e in store.events_since(0) if e["kind"] == "mcp.client"][-1]["payload"]
    assert cl["token_name"] == "Test IDE" and cl["token_status"] == "active"
    # a refresh gives a new access token, the connection keeps its identity
    tr = GrantService(oauth_ctx).refresh({"grant_type": "refresh_token", "refresh_token": m.refresh_token,
                                          "client_id": m.client_id}, "127.0.0.1", time.time())
    assert tr.grant_id == m.grant_id
    mcp_list_tools(live_oauth, tr.access_token)
    assert len(store.list_mcp_clients()) == 1 and store.list_mcp_clients()[0]["request_count"] >= 2
    assert {c["token_id"] for c in store.list_mcp_calls(limit=20) if c["method"] == "tools/list"} == {m.grant_id}


def test_mcp_jobs_attributed_to_grant(live_oauth, oauth_ctx):
    m = mint_tokens(oauth_ctx, scopes=("doc4ai:read", "doc4ai:convert"), resource=_res(live_oauth))
    pdf = base64.b64encode(b"%PDF-1.4\n%fake\n").decode()
    res = mcp_call(live_oauth, m.access_token, "convert_document", {"filename": "a.pdf", "content_base64": pdf})
    assert not res.is_error, res.content
    job_id = res.structured_content["job_id"]
    row = oauth_ctx.store._q1("SELECT token_id FROM mcp_jobs WHERE job_id=?", (job_id,))
    assert row["token_id"] == m.grant_id


def test_revoked_grant_status_in_client_events(live_oauth, oauth_ctx):
    from aidoc.mcp.calllog import CallRecorder
    m = mint_tokens(oauth_ctx, resource=_res(live_oauth))
    mcp_list_tools(live_oauth, m.access_token)
    GrantService(oauth_ctx).revoke_grant(m.grant_id, "admin", time.time())
    row = oauth_ctx.store.list_mcp_clients()[0]
    payload = CallRecorder(oauth_ctx)._client_payload(row, "idle")
    assert payload["token_name"] == "Test IDE" and payload["token_status"] == "revoked"
