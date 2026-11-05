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


# ---- part 2 (Task 10): admin API

def _pat(ctx, name="my pat"):
    from aidoc.mcp import tokens as PT
    raw = PT.generate_token()
    tid = ctx.store.create_api_token(name=name, prefix=PT.display_prefix(raw), token_hash=PT.token_hash(ctx.extras["mcp"].secret, raw),
                                     scopes=["doc4ai:read"], expires_at=None)
    return raw, tid


def test_tokens_list_merges_pats_and_grants(oauth_client, oauth_ctx, dcr_client):
    _raw, pat_id = _pat(oauth_ctx)
    m = mint_tokens(oauth_ctx, scopes=("doc4ai:read", "doc4ai:convert"), client_id=dcr_client["client_id"])
    oauth_ctx.store.insert_mcp_call(status="ok", token_id=m.grant_id, method="tools/list")
    toks = oauth_client.get("/api/mcp/tokens").json()["tokens"]
    assert [t["kind"] for t in toks] == ["oauth", "pat"]                    # newest first
    pat = toks[1]
    assert pat["id"] == pat_id and pat["name"] == "my pat" and "token_hash" not in pat
    g = toks[0]
    grant = oauth_ctx.store.get_oauth_grant(m.grant_id)
    assert g["id"] == m.grant_id and g["name"] == "Test IDE" and g["prefix"] == dcr_client["client_id"][:14]
    assert g["scopes"] == ["doc4ai:read", "doc4ai:convert"] and g["note"] == "127.0.0.1" and g["status"] == "active"
    assert g["expires_at"] == grant["absolute_expires_at"] and g["refresh_expires_at"] > time.time()
    assert g["oauth"] == {"client_id": dcr_client["client_id"], "kind": "dcr", "redirect_uri": "http://127.0.0.1:3118/callback",
                          "access_tokens_active": 1}
    assert g["calls_24h"] == 1 and g["errors_24h"] == 0 and g["rate_limit_per_min"] is None and g["rotated_from"] is None
    for k in ("created_at", "revoked_at", "revoked_reason", "last_used_at", "last_used_ip", "last_client"):
        assert k in g
    assert not any("hash" in k for k in g)


def test_cimd_grant_prefix_is_host(oauth_client, oauth_ctx, fake_cimd):
    from tests.oauth.conftest import CLAUDE_CODE_CIMD
    fake_cimd.put(CLAUDE_CODE_CIMD["client_id"], CLAUDE_CODE_CIMD)
    from aidoc.oauth.clients import ClientRegistry
    ClientRegistry(oauth_ctx).resolve(CLAUDE_CODE_CIMD["client_id"], time.time())
    m = mint_tokens(oauth_ctx, client_id=CLAUDE_CODE_CIMD["client_id"], redirect="http://localhost:51234/callback")
    g = next(t for t in oauth_client.get("/api/mcp/tokens").json()["tokens"] if t["id"] == m.grant_id)
    assert g["name"] == "Claude Code" and g["prefix"] == "claude.ai" and g["note"] == "localhost" and g["oauth"]["kind"] == "cimd"


def test_admin_revoke_grant(oauth_client, oauth_ctx, dcr_client):
    m = mint_tokens(oauth_ctx, client_id=dcr_client["client_id"])
    r = oauth_client.post(f"/api/mcp/tokens/{m.grant_id}/revoke", json={})
    assert r.status_code == 200 and r.json()["token"]["status"] == "revoked" and r.json()["token"]["kind"] == "oauth"
    assert oauth_ctx.store.get_oauth_grant(m.grant_id)["revoked_reason"] == "admin"
    assert all(t["revoked_at"] is not None for t in oauth_ctx.store._qa("SELECT * FROM oauth_tokens WHERE grant_id=?", (m.grant_id,)))
    ev = [e for e in oauth_ctx.store.events_since(0) if e["kind"] == "mcp.token"][-1]["payload"]
    assert ev["action"] == "revoked" and ev["kind"] == "oauth" and ev["id"] == m.grant_id
    assert oauth_client.post(f"/api/mcp/tokens/{m.grant_id}/revoke", json={}).status_code == 409
    for call in (lambda: oauth_client.patch(f"/api/mcp/tokens/{m.grant_id}", json={"name": "x"}),
                 lambda: oauth_client.post(f"/api/mcp/tokens/{m.grant_id}/rotate")):
        r = call()
        assert r.status_code == 409 and r.json()["error"] == "oauth_grant"
    assert oauth_client.post("/api/mcp/tokens/nope/revoke", json={}).status_code == 404


def test_clients_and_calls_carry_token_kind(live_oauth, oauth_ctx):
    from fastapi.testclient import TestClient

    from aidoc.server.app import create_app
    m = mint_tokens(oauth_ctx, resource=live_oauth + "/mcp")
    mcp_list_tools(live_oauth, m.access_token)
    with TestClient(create_app(oauth_ctx), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 1)) as c:
        cl = c.get("/api/mcp/clients").json()["clients"][0]
        assert cl["token_name"] == "Test IDE" and cl["token_kind"] == "oauth" and cl["token_status"] == "active"
        call = next(x for x in c.get("/api/mcp/calls").json()["calls"] if x["method"] == "tools/list")
        assert call["token_name"] == "Test IDE" and call["token_kind"] == "oauth"
        st = c.get("/api/mcp/stats").json()
        assert any(t["token_id"] == m.grant_id and t["name"] == "Test IDE" for t in st["tokens"])
        GrantService(oauth_ctx).revoke_grant(m.grant_id, "admin", time.time())
        assert c.get("/api/mcp/clients").json()["clients"][0]["token_status"] == "revoked"


def test_status_oauth_and_snippets(oauth_client, oauth_ctx, dcr_client):
    mint_tokens(oauth_ctx, client_id=dcr_client["client_id"])
    st = oauth_client.get("/api/mcp/status").json()
    assert st["oauth"] == {"enabled": True, "allow_dcr": True, "allow_cimd": True, "issuer_example": "http://127.0.0.1:8765",
                           "grants_active": 1, "clients": 1}
    sn = oauth_client.get("/api/mcp/config-snippets", params={"auth": "oauth"}).json()
    texts = " ".join(s["text"] for s in sn["snippets"])
    assert sn["auth"] == "oauth" and "Bearer" not in texts and "doc4ai_pat_" not in texts and "<YOUR_TOKEN>" not in texts
    assert [s["client"] for s in sn["snippets"]] == ["claude-code", "cursor", "vscode", "inspector"]
    assert sn["snippets"][0]["text"].startswith("claude mcp add --transport http doc4ai http://127.0.0.1:8765/mcp")
    pat = oauth_client.get("/api/mcp/config-snippets").json()
    assert pat["auth"] == "pat" and "Bearer" in pat["snippets"][0]["text"]
    assert oauth_client.get("/api/mcp/config-snippets", params={"auth": "jwt"}).status_code == 422


def test_admin_routes_need_server_token(token_oauth_client, token_oauth_ctx):
    for path in ("/api/mcp/tokens", "/api/mcp/status", "/api/mcp/config-snippets?auth=oauth"):
        assert token_oauth_client.get(path).status_code == 401
        assert token_oauth_client.get(path, headers={"Authorization": "Bearer admin-s3cret"}).status_code == 200
