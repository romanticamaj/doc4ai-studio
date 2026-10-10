import pytest

from tests.compat.conftest import last_call


def test_capabilities_is_public(compat_env):
    r = compat_env.client.get("/v1/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["to_formats"] == ["md", "text"] and "workspace" not in body and "doc4ai" in body
    assert body["doc4ai"]["sync_timeout_s"] == 600 and all(isinstance(v, bool) for v in body["engines"].values())
    assert last_call(compat_env.ctx)["tool_name"] == "capabilities"


@pytest.mark.parametrize("header", ["X-Api-Key", "Authorization"])
def test_token_accepted_in_either_header(compat_env, header):
    raw, tid = compat_env.issue()
    value = raw if header == "X-Api-Key" else f"Bearer {raw}"
    r = compat_env.client.get("/v1/_whoami", headers={header: value})
    assert r.status_code == 200 and r.json()["token_id"] == tid
    row = last_call(compat_env.ctx)
    assert row["method"] == "compat/docling" and row["status"] == "ok" and row["token_id"] == tid
    assert row["protocol_version"] == "docling-serve-v1"


def test_two_different_tokens_is_401(compat_env):
    a, _ = compat_env.issue()
    b, _ = compat_env.issue()
    r = compat_env.client.get("/v1/_whoami", headers={"X-Api-Key": a, "Authorization": f"Bearer {b}"})
    assert r.status_code == 401 and r.json() == {"detail": "X-Api-Key and Authorization disagree"}
    same = compat_env.client.get("/v1/_whoami", headers={"X-Api-Key": a, "Authorization": f"Bearer {a}"})
    assert same.status_code == 200


def test_missing_bad_revoked_expired(compat_env):
    c = compat_env.client
    r = c.get("/v1/_whoami")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith('Bearer realm="doc4ai", error="invalid_token"')
    assert "resource_metadata" not in r.headers["www-authenticate"]
    assert last_call(compat_env.ctx)["error_code"] == "missing_token"
    raw, tid = compat_env.issue()
    compat_env.ctx.store.revoke_api_token(tid, "test", at=0)
    r = c.get("/v1/_whoami", headers={"X-Api-Key": raw})
    assert r.status_code == 401 and r.json()["detail"] == "This token was revoked"
    assert last_call(compat_env.ctx)["token_id"] == tid          # known token: attributed (MCP A phase item 14)
    raw2, _ = compat_env.issue(expires_in=-5)
    assert c.get("/v1/_whoami", headers={"X-Api-Key": raw2}).status_code == 401


def test_token_in_query_is_400(compat_env):
    raw, _ = compat_env.issue()
    r = compat_env.client.get("/v1/_whoami", params={"api_key": raw})
    assert r.status_code == 400 and "X-Api-Key" in r.json()["detail"]
    row = last_call(compat_env.ctx)
    assert row["error_code"] == "token_in_query" and raw not in (row["token_prefix_seen"] or "")


def test_scope_enforced(compat_env):
    raw, _ = compat_env.issue(scopes=("doc4ai:read",))
    r = compat_env.client.get("/v1/_whoami", headers={"X-Api-Key": raw})     # _whoami needs convert
    assert r.status_code == 403 and r.json() == {"detail": "token lacks doc4ai:convert"}
    assert last_call(compat_env.ctx)["status"] == "forbidden_scope"


def test_cross_origin_is_403_and_server_token_is_not_a_pat(compat_env):
    raw, _ = compat_env.issue()
    r = compat_env.client.get("/v1/_whoami", headers={"X-Api-Key": raw, "Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json() == {"detail": "cross-origin request"}
    r = compat_env.client.get("/v1/_whoami", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 401


def test_pat_never_authorizes_api(token_ctx):
    """A PAT on /api/* is still 401 even though /v1 now accepts it (MCP spec 2.1 isolation)."""
    from fastapi.testclient import TestClient

    from aidoc.mcp import tokens as T
    from aidoc.mcp.tokens import load_or_create_secret
    from aidoc.server.app import create_app
    secret = load_or_create_secret(token_ctx.config.data_dir)
    raw = T.generate_token()
    token_ctx.store.create_api_token(name="t", prefix=T.display_prefix(raw), token_hash=T.token_hash(secret, raw),
                                     scopes=["doc4ai:read", "doc4ai:convert", "doc4ai:manage"], expires_at=None)
    with TestClient(create_app(token_ctx), client=("10.0.0.9", 5000), base_url="http://127.0.0.1:8765") as c:
        assert c.get("/api/system", headers={"Authorization": f"Bearer {raw}"}).status_code == 401
        assert c.get("/api/system", headers={"X-Api-Key": raw}).status_code == 401


def test_auth_flood_429(compat_env):
    bad = "doc4ai_pat_" + "x" * 50
    for _ in range(10):
        compat_env.client.get("/v1/_whoami", headers={"X-Api-Key": bad})
    r = compat_env.client.get("/v1/_whoami", headers={"X-Api-Key": bad})
    assert r.status_code == 429 and r.headers["retry-after"] and "detail" in r.json()


def test_unknown_v1_route_is_docling_404_not_spa(compat_env):
    r = compat_env.client.get("/v1/nope")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}


def test_disabled_compat_is_404_but_health_ok(compat_env):
    compat_env.ctx.config.compat.enabled = False
    r = compat_env.client.get("/v1/capabilities")
    assert r.status_code == 404 and r.json() == {"detail": "compat API disabled"}
    assert compat_env.client.get("/health").status_code == 200
