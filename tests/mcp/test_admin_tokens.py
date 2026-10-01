import time

from aidoc.mcp import tokens as T
from aidoc.mcp.principal import PatVerifier, Principal
from aidoc.mcp.tokens import load_or_create_secret


def _events(ctx, kind):
    return [e["payload"] for e in ctx.store.events_since(0) if e["kind"] == kind]


def test_status_shape(client, ctx):
    ctx.extras["bind_host"], ctx.extras["bind_port"] = "127.0.0.1", 8765
    s = client.get("/api/mcp/status").json()
    assert s["enabled"] is True and s["endpoint_urls"] == ["http://127.0.0.1:8765/mcp"]
    assert s["bind"] == {"host": "127.0.0.1", "port": 8765} and "127.0.0.1:*" in s["allowed_hosts"]
    assert s["protocol_versions"] == ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"]
    assert s["sdk_version"].startswith("2.2") and s["active_clients"] == 0 and s["calls_24h"] == 0 and s["errors_24h"] == 0
    assert s["tokens_expiring_soon"] == 0 and s["local_path_roots"] == [] and s["plaintext_http"] is True
    assert s["tokenizer"] in ("trigram", "unicode61") and s["workspace"] == "default"


def test_create_token_returns_plaintext_once_and_verifies(client, ctx):
    r = client.post("/api/mcp/tokens", json={"name": "Claude Code @ laptop", "scopes": ["doc4ai:read", "doc4ai:convert"],
                                             "expires_in_days": 30, "note": "n", "rate_limit_per_min": 120})
    assert r.status_code == 201, r.text
    body = r.json()
    raw, rec = body["token"], body["record"]
    assert T.is_well_formed(raw) and rec["prefix"] == T.display_prefix(raw) and "token_hash" not in rec
    assert rec["name"] == "Claude Code @ laptop" and rec["scopes"] == ["doc4ai:read", "doc4ai:convert"] and rec["status"] == "active"
    assert 29.9 * 86400 < rec["expires_at"] - rec["created_at"] <= 30 * 86400 + 5 and rec["rate_limit_per_min"] == 120
    assert rec["calls_24h"] == 0 and rec["errors_24h"] == 0 and isinstance(body["snippets"], list)
    listed = client.get("/api/mcp/tokens").json()["tokens"]
    assert [t["id"] for t in listed] == [rec["id"]] and "token" not in listed[0] and "token_hash" not in listed[0]
    p = PatVerifier(ctx.store, load_or_create_secret(ctx.config.data_dir)).verify(raw)
    assert isinstance(p, Principal) and p.token_id == rec["id"]
    assert _events(ctx, "mcp.token")[-1] == {"id": rec["id"], "name": rec["name"], "prefix": rec["prefix"], "action": "created"}


def test_create_token_validation(client, ctx):
    bad = [({"name": "x", "scopes": ["doc4ai:convert"]}, 422, "invalid_scopes"),              # read is mandatory
           ({"name": "x", "scopes": ["doc4ai:read", "bogus"]}, 422, "invalid_scopes"),
           ({"name": "x", "scopes": ["doc4ai:read"], "expires_in_days": 400}, 422, "ttl_too_long"),
           ({"name": "x", "scopes": ["doc4ai:read"], "expires_in_days": 0}, 422, "no_expiry_disabled"),
           ({"name": "", "scopes": ["doc4ai:read"]}, 422, "validation_error"),
           ({"scopes": ["doc4ai:read"]}, 422, "validation_error")]
    for body, status, code in bad:
        r = client.post("/api/mcp/tokens", json=body)
        assert (r.status_code, r.json()["error"]) == (status, code), (body, r.text)
    r = client.post("/api/mcp/tokens", json={"name": "x", "scopes": ["doc4ai:read"], "expires_in_days": 400})
    assert r.json()["max"] == 365
    ctx.config.mcp.allow_no_expiry = True
    r = client.post("/api/mcp/tokens", json={"name": "forever", "scopes": ["doc4ai:read"], "expires_in_days": 0})
    assert r.status_code == 201 and r.json()["record"]["expires_at"] is None
    ctx.config.mcp.allow_no_expiry = False
    r = client.post("/api/mcp/tokens", json={"name": "default ttl", "scopes": ["doc4ai:read"]})
    rec = r.json()["record"]
    assert 89.9 * 86400 < rec["expires_at"] - rec["created_at"] <= 90 * 86400 + 5


def test_patch_revoke_rotate(client, ctx):
    created = client.post("/api/mcp/tokens", json={"name": "a", "scopes": ["doc4ai:read", "doc4ai:manage"], "note": "old"}).json()
    tid, raw = created["record"]["id"], created["token"]
    r = client.patch(f"/api/mcp/tokens/{tid}", json={"name": "renamed", "note": None, "rate_limit_per_min": 5})
    assert r.status_code == 200 and r.json()["token"]["name"] == "renamed" and r.json()["token"]["note"] is None
    assert r.json()["token"]["rate_limit_per_min"] == 5
    assert _events(ctx, "mcp.token")[-1]["action"] == "updated"
    r = client.patch(f"/api/mcp/tokens/{tid}", json={"scopes": ["doc4ai:read"]})
    assert r.status_code == 422 and r.json()["error"] == "scopes_immutable"
    assert client.patch("/api/mcp/tokens/nope", json={"name": "x"}).status_code == 404
    # rotate: new plaintext, same name/scopes/note/limit, old revoked with reason "rotated"
    r = client.post(f"/api/mcp/tokens/{tid}/rotate")
    assert r.status_code == 201, r.text
    rot = r.json()
    assert T.is_well_formed(rot["token"]) and rot["token"] != raw
    new = rot["record"]
    assert new["id"] != tid and new["rotated_from"] == tid and new["name"] == "renamed" and new["scopes"] == ["doc4ai:read", "doc4ai:manage"]
    assert new["rate_limit_per_min"] == 5 and rot["revoked"]["id"] == tid and rot["revoked"]["revoked_reason"] == "rotated"
    assert rot["revoked"]["status"] == "revoked" and _events(ctx, "mcp.token")[-1]["action"] == "rotated"
    v = PatVerifier(ctx.store, load_or_create_secret(ctx.config.data_dir))
    assert v.verify(raw).reason == "revoked" and isinstance(v.verify(rot["token"]), Principal)
    assert client.post(f"/api/mcp/tokens/{tid}/rotate").status_code == 409
    # revoke the new one with a reason
    r = client.post(f"/api/mcp/tokens/{new['id']}/revoke", json={"reason": "lost laptop"})
    assert r.status_code == 200 and r.json()["token"]["status"] == "revoked" and r.json()["token"]["revoked_reason"] == "lost laptop"
    assert client.post(f"/api/mcp/tokens/{new['id']}/revoke", json={}).status_code == 409
    assert _events(ctx, "mcp.token")[-1]["action"] == "revoked"
    assert client.post("/api/mcp/tokens/nope/revoke", json={}).status_code == 404


def test_token_list_counts_and_expiring_soon(client, ctx):
    rec = client.post("/api/mcp/tokens", json={"name": "a", "scopes": ["doc4ai:read"], "expires_in_days": 7}).json()["record"]
    ctx.store.insert_mcp_call(status="ok", token_id=rec["id"], method="tools/list")
    ctx.store.insert_mcp_call(status="tool_error", token_id=rec["id"], method="tools/call", tool_name="read_document")
    ctx.store.insert_mcp_call(status="ok", token_id=rec["id"], method="tools/list", ts=time.time() - 2 * 86400)
    t = client.get("/api/mcp/tokens").json()["tokens"][0]
    assert t["calls_24h"] == 2 and t["errors_24h"] == 1
    assert client.get("/api/mcp/status").json()["tokens_expiring_soon"] == 1          # 7 days < 14
    ctx.store.update_api_token(rec["id"], expires_at=time.time() - 1)
    assert client.get("/api/mcp/tokens").json()["tokens"][0]["status"] == "expired"


def test_admin_api_needs_admin_auth(token_ctx):
    from fastapi.testclient import TestClient

    from aidoc.server.app import create_app
    with TestClient(create_app(token_ctx), base_url="http://192.168.1.20:8765") as c:
        assert c.get("/api/mcp/tokens").status_code == 401
        assert c.post("/api/mcp/tokens", json={"name": "x", "scopes": ["doc4ai:read"]}).status_code == 401
        assert c.get("/api/mcp/tokens", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_patch_rejects_bad_bodies_with_422(client, ctx):
    tid = client.post("/api/mcp/tokens", json={"name": "a", "scopes": ["doc4ai:read"]}).json()["record"]["id"]
    for body in ([1, 2], {"rate_limit_per_min": 0}, {"bogus": 1}, {"name": None}, {"name": ""}):
        r = client.patch(f"/api/mcp/tokens/{tid}", json=body)
        assert (r.status_code, r.json()["error"]) == (422, "validation_error"), (body, r.text)
    assert client.get("/api/mcp/tokens").json()["tokens"][0]["name"] == "a"
