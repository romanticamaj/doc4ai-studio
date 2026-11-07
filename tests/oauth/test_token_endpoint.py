"""Token endpoint (spec 2026-11-05 §5.3 (4)–(6)): code exchange with PKCE, refresh rotation + replay, revocation."""
import time

import pytest

from doc4ai.oauth import tokens as OT
from tests.oauth.conftest import RES, pkce

CB = "http://127.0.0.1:3118/callback"
DAY = 86400


def svc(ctx):
    from doc4ai.oauth.grants import GrantService
    return GrantService(ctx)


def issue(ctx, row, challenge, scopes=("doc4ai:read",), redirect=CB, resource=RES):
    return svc(ctx).issue_code(row, redirect, challenge, list(scopes), resource, "127.0.0.1", time.time())


def token(client, **form):
    return client.post("/oauth/token", data=form)


def code_form(row, raw_code, v, **over):
    f = {"grant_type": "authorization_code", "code": raw_code, "code_verifier": v, "redirect_uri": CB,
         "client_id": row["client_id"], "resource": RES}
    f.update(over)
    return {k: v for k, v in f.items() if v is not None}


def _hash(ctx, raw):
    return OT.hash_(ctx.extras["mcp"].secret, raw)


def test_code_exchange_issues_rotating_tokens(oauth_client, oauth_ctx, dcr_client):
    v, c = pkce()
    code = issue(oauth_ctx, dcr_client, c)
    assert OT.is_well_formed(OT.CODE_PREFIX, code)
    stored = oauth_ctx.store._q1("SELECT * FROM oauth_codes")
    assert stored["expires_at"] - stored["created_at"] == 300 and stored["code_hash"] == _hash(oauth_ctx, code)
    r = token(oauth_client, **code_form(dcr_client, code, v))
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and r.headers["pragma"] == "no-cache", r.text
    body = r.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "refresh_token", "scope"}
    assert body["token_type"] == "Bearer" and OT.is_well_formed(OT.AT_PREFIX, body["access_token"])
    assert OT.is_well_formed(OT.RT_PREFIX, body["refresh_token"]) and body["expires_in"] == 3600 and body["scope"] == "doc4ai:read"
    at = oauth_ctx.store.get_oauth_token_by_hash(_hash(oauth_ctx, body["access_token"]))
    rt = oauth_ctx.store.get_oauth_token_by_hash(_hash(oauth_ctx, body["refresh_token"]))
    assert at["kind"] == "at" and rt["kind"] == "rt" and at["grant_id"] == rt["grant_id"] and at["resource"] == RES
    assert round(rt["expires_at"] - rt["created_at"]) == 30 * DAY
    grant = oauth_ctx.store.get_oauth_grant(at["grant_id"])
    assert round(grant["absolute_expires_at"] - grant["created_at"]) == 90 * DAY and grant["client_id"] == dcr_client["client_id"]
    r2 = token(oauth_client, grant_type="refresh_token", refresh_token=body["refresh_token"], client_id=dcr_client["client_id"])
    assert r2.status_code == 200 and r2.json()["refresh_token"] != body["refresh_token"]
    assert r2.json()["access_token"] != body["access_token"] and r2.json()["scope"] == "doc4ai:read"
    rt2 = oauth_ctx.store.get_oauth_token_by_hash(_hash(oauth_ctx, r2.json()["refresh_token"]))
    assert rt2["grant_id"] == at["grant_id"] and rt2["parent_id"] == rt["id"]
    # replay of the rotated refresh token kills the whole grant (RFC 9700 §4.14.2)
    r3 = token(oauth_client, grant_type="refresh_token", refresh_token=body["refresh_token"], client_id=dcr_client["client_id"])
    assert r3.status_code == 400 and r3.json()["error"] == "invalid_grant"
    r4 = token(oauth_client, grant_type="refresh_token", refresh_token=r2.json()["refresh_token"], client_id=dcr_client["client_id"])
    assert r4.status_code == 400 and r4.json()["error"] == "invalid_grant"
    g = oauth_ctx.store.get_oauth_grant(at["grant_id"])
    assert g["revoked_reason"] == "refresh_replay"
    assert all(t["revoked_at"] is not None for t in oauth_ctx.store._qa("SELECT * FROM oauth_tokens"))


def test_code_reuse_revokes_grant(oauth_client, oauth_ctx, dcr_client):
    v, c = pkce()
    code = issue(oauth_ctx, dcr_client, c)
    first = token(oauth_client, **code_form(dcr_client, code, v)).json()
    again = token(oauth_client, **code_form(dcr_client, code, v))
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    assert token(oauth_client, grant_type="refresh_token", refresh_token=first["refresh_token"],
                 client_id=dcr_client["client_id"]).status_code == 400
    g = oauth_ctx.store.list_oauth_grants()[0]
    assert g["revoked_reason"] == "code_reuse"
    assert all(t["revoked_at"] is not None for t in oauth_ctx.store._qa("SELECT * FROM oauth_tokens"))


@pytest.mark.parametrize("over,error", [
    ({"code_verifier": "wrong" * 10}, "invalid_grant"),
    ({"code_verifier": "short"}, "invalid_grant"),                              # RFC 7636 length
    ({"code_verifier": "a" * 129}, "invalid_grant"),
    ({"code_verifier": None}, "invalid_request"),
    ({"redirect_uri": "http://127.0.0.1:3119/callback"}, "invalid_grant"),      # loopback: exact incl. port at the token step
    ({"redirect_uri": None}, "invalid_request"),
    ({"client_id": "doc4ai_cl_someoneelse0000000"}, "invalid_grant"),
    ({"client_id": None}, "invalid_request"),
    ({"resource": "http://127.0.0.1:8765/other"}, "invalid_target"),
    ({"code": "doc4ai_ac_garbage"}, "invalid_grant"),
    ({"code": None}, "invalid_request"),
    ({"grant_type": "client_credentials"}, "unsupported_grant_type"),
    ({"grant_type": None}, "invalid_request"),
])
def test_token_request_errors(oauth_client, oauth_ctx, dcr_client, over, error):
    v, c = pkce()
    code = issue(oauth_ctx, dcr_client, c)
    r = token(oauth_client, **code_form(dcr_client, code, v, **over))
    assert r.status_code == 400 and r.json()["error"] == error and r.headers["cache-control"] == "no-store", r.text
    assert set(r.json()) == {"error", "error_description"}


def test_resource_omitted_is_accepted(oauth_client, oauth_ctx, dcr_client):
    v, c = pkce()
    code = issue(oauth_ctx, dcr_client, c)
    r = token(oauth_client, **code_form(dcr_client, code, v, resource=None))
    assert r.status_code == 200, r.text


def test_code_expires_and_bad_bodies(oauth_client, oauth_ctx, dcr_client):
    v, c = pkce()
    code = issue(oauth_ctx, dcr_client, c)
    oauth_ctx.store.con.execute("UPDATE oauth_codes SET expires_at=?", (time.time() - 1,))
    r = token(oauth_client, **code_form(dcr_client, code, v))
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    r = oauth_client.post("/oauth/token", json={"grant_type": "authorization_code"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"
    r = oauth_client.post("/oauth/token", content=b"grant_type=refresh_token&grant_type=authorization_code",
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"            # RFC 6749 §3.2: no repeats


def test_pat_is_not_an_oauth_credential(oauth_client, oauth_ctx, dcr_client):
    from doc4ai.mcp import tokens as T
    r = token(oauth_client, grant_type="refresh_token", refresh_token=T.generate_token(), client_id=dcr_client["client_id"])
    assert r.status_code == 400 and r.json() == {"error": "invalid_grant", "error_description": "refresh token is invalid, expired or revoked"}
    at = OT.generate(OT.AT_PREFIX)
    r = token(oauth_client, grant_type="refresh_token", refresh_token=at, client_id=dcr_client["client_id"])
    assert r.json()["error"] == "invalid_grant"


def test_refresh_scope_narrowing_and_limits(oauth_client, oauth_ctx, dcr_client):
    v, c = pkce()
    code = issue(oauth_ctx, dcr_client, c, scopes=("doc4ai:read", "doc4ai:convert"))
    first = token(oauth_client, **code_form(dcr_client, code, v)).json()
    assert first["scope"] == "doc4ai:read doc4ai:convert"
    r = token(oauth_client, grant_type="refresh_token", refresh_token=first["refresh_token"], client_id=dcr_client["client_id"],
              scope="doc4ai:read")
    assert r.json()["scope"] == "doc4ai:read"
    r = token(oauth_client, grant_type="refresh_token", refresh_token=r.json()["refresh_token"], client_id=dcr_client["client_id"],
              scope="doc4ai:read doc4ai:manage")
    assert r.status_code == 400 and r.json()["error"] == "invalid_scope"


def test_refresh_errors(oauth_client, oauth_ctx, dcr_client):
    v, c = pkce()
    code = issue(oauth_ctx, dcr_client, c)
    first = token(oauth_client, **code_form(dcr_client, code, v)).json()
    rt = first["refresh_token"]
    r = token(oauth_client, grant_type="refresh_token", refresh_token=rt, client_id="doc4ai_cl_other")
    assert r.json()["error"] == "invalid_grant"
    r = token(oauth_client, grant_type="refresh_token", refresh_token=rt, client_id=dcr_client["client_id"],
              resource="http://localhost:8765/mcp")
    assert r.json()["error"] == "invalid_target"
    r = token(oauth_client, grant_type="refresh_token", client_id=dcr_client["client_id"])
    assert r.json()["error"] == "invalid_request"
    # past the absolute limit (90 days from the grant) the refresh token is dead even if its own expiry is later
    oauth_ctx.store.con.execute("UPDATE oauth_grants SET absolute_expires_at=?", (time.time() - 1,))
    r = token(oauth_client, grant_type="refresh_token", refresh_token=rt, client_id=dcr_client["client_id"])
    assert r.json()["error"] == "invalid_grant"


def test_refresh_expiry_never_passes_absolute(oauth_client, oauth_ctx, dcr_client):
    v, c = pkce()
    code = issue(oauth_ctx, dcr_client, c)
    first = token(oauth_client, **code_form(dcr_client, code, v)).json()
    lim = time.time() + 5 * DAY
    oauth_ctx.store.con.execute("UPDATE oauth_grants SET absolute_expires_at=?", (lim,))
    r = token(oauth_client, grant_type="refresh_token", refresh_token=first["refresh_token"], client_id=dcr_client["client_id"])
    rt = oauth_ctx.store.get_oauth_token_by_hash(_hash(oauth_ctx, r.json()["refresh_token"]))
    assert rt["expires_at"] == lim


def test_revoke_endpoint_always_200(oauth_client, oauth_ctx, dcr_client):
    for which in ("access_token", "refresh_token"):
        v, c = pkce()
        code = issue(oauth_ctx, dcr_client, c)
        body = token(oauth_client, **code_form(dcr_client, code, v)).json()
        r = oauth_client.post("/oauth/revoke", data={"token": body[which]})
        assert r.status_code == 200 and r.json() == {} and r.headers["cache-control"] == "no-store"
        gid = oauth_ctx.store.get_oauth_token_by_hash(_hash(oauth_ctx, body["access_token"]))["grant_id"]
        assert oauth_ctx.store.get_oauth_grant(gid)["revoked_reason"] == "client_revoke"
        rows = oauth_ctx.store._qa("SELECT * FROM oauth_tokens WHERE grant_id=?", (gid,))
        assert rows and all(t["revoked_at"] is not None for t in rows)
        assert token(oauth_client, grant_type="refresh_token", refresh_token=body["refresh_token"],
                     client_id=dcr_client["client_id"]).json()["error"] == "invalid_grant"
    assert oauth_client.post("/oauth/revoke", data={"token": "garbage"}).status_code == 200
    assert oauth_client.post("/oauth/revoke", data={}).status_code == 200


def test_revoke_publishes_sse(oauth_ctx, dcr_client):
    from tests.oauth.conftest import mint_tokens
    m = mint_tokens(oauth_ctx, client_id=dcr_client["client_id"])
    created = [e for e in oauth_ctx.store.events_since(0) if e["kind"] == "mcp.token"]
    assert created[-1]["payload"]["action"] == "created" and created[-1]["payload"]["kind"] == "oauth"
    assert created[-1]["payload"]["id"] == m.grant_id and created[-1]["payload"]["name"] == "Test IDE"
    assert svc(oauth_ctx).revoke_grant(m.grant_id, "admin", time.time()) == 2
    ev = [e for e in oauth_ctx.store.events_since(0) if e["kind"] == "mcp.token"][-1]["payload"]
    assert ev == {"id": m.grant_id, "name": "Test IDE", "prefix": dcr_client["client_id"][:14], "action": "revoked", "kind": "oauth"}
    assert svc(oauth_ctx).revoke_grant(m.grant_id, "admin", time.time()) == 0


def test_token_flood_429(oauth_client, oauth_ctx, dcr_client):
    for _ in range(10):
        r = token(oauth_client, grant_type="authorization_code", code=OT.generate(OT.CODE_PREFIX), code_verifier="x" * 43,
                  redirect_uri=CB, client_id=dcr_client["client_id"])
        assert r.status_code == 400
    r = token(oauth_client, grant_type="authorization_code", code=OT.generate(OT.CODE_PREFIX), code_verifier="x" * 43,
              redirect_uri=CB, client_id=dcr_client["client_id"])
    assert r.status_code == 429 and "retry-after" in r.headers and r.json()["error"] == "invalid_grant"


def test_disabled_and_unknown_host(oauth_client, oauth_ctx):
    assert oauth_client.post("/oauth/token", data={"grant_type": "x"}, headers={"Host": "evil.test"}).status_code in (400, 403)
    oauth_ctx.config.oauth.enabled = False
    assert oauth_client.post("/oauth/token", data={"grant_type": "x"}).status_code == 404
    assert oauth_client.post("/oauth/revoke", data={"token": "x"}).status_code == 404
