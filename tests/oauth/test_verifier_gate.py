"""Verifier chain, audience binding and the /mcp 401 shape (spec 2026-11-05 §4.5, §7.1)."""
import re
import time

import httpx

from aidoc.mcp import tokens as PT
from aidoc.oauth import tokens as OT
from tests.mcp.conftest import mcp_call, mcp_list_tools, raw_post
from tests.oauth.conftest import LIST, RES, mint_tokens

READ_TOOLS = {"search_library", "list_documents", "get_document_info", "read_document", "get_chunks", "get_job",
              "verify_document"}


def _res(base):
    return base + "/mcp"


def _www(r):
    return r.headers["www-authenticate"]


def test_chain_dispatches_by_prefix(oauth_client, oauth_ctx):
    from aidoc.mcp.principal import AuthFailure, Principal, VerifierChain
    chain = oauth_ctx.extras["mcp"].verifier
    assert isinstance(chain, VerifierChain)
    m = mint_tokens(oauth_ctx, scopes=("doc4ai:read", "doc4ai:convert"))
    p = chain.verify(m.access_token, resource=RES)
    assert isinstance(p, Principal) and p.kind == "oauth" and p.token_id == m.grant_id and p.client_id == m.client_id
    assert p.subject == "owner" and p.scopes == frozenset({"doc4ai:read", "doc4ai:convert"}) and p.name == "Test IDE"
    assert p.rate_limit_per_min == oauth_ctx.config.mcp.rate_limit_per_min and p.expires_at > time.time()
    assert chain.verify(m.access_token, resource="http://localhost:8765/mcp").reason == "wrong_audience"
    assert chain.verify(m.access_token).reason == "wrong_audience"                    # no resource known: never accepted
    assert chain.verify(m.refresh_token, resource=RES).reason == "bad_format"         # a refresh token is no bearer credential
    assert chain.verify("eyJhbGciOiJSUzI1NiJ9.e30.sig", resource=RES).reason == "bad_format"
    assert chain.verify(m.access_token[:-1] + ("A" if m.access_token[-1] != "A" else "B"), resource=RES).reason == "bad_checksum"
    assert chain.verify(OT.generate(OT.AT_PREFIX), resource=RES).reason == "unknown_token"
    assert chain.verify(None).reason == "missing_token"
    r = chain.verify(PT.generate_token(), resource=RES)
    assert isinstance(r, AuthFailure) and r.reason == "unknown_token"                 # the PAT branch still answers
    assert chain.verify(m.refresh_token, resource=RES).prefix_seen == m.refresh_token[:14]
    oauth_ctx.store.con.execute("UPDATE oauth_tokens SET expires_at=? WHERE kind='at'", (time.time() - 1,))
    r = chain.verify(m.access_token, resource=RES)
    assert r.reason == "expired" and r.token_id == m.grant_id
    from aidoc.oauth.grants import GrantService
    GrantService(oauth_ctx).revoke_grant(m.grant_id, "admin", time.time())
    assert chain.verify(m.access_token, resource=RES).reason == "revoked"
    oauth_ctx.config.oauth.enabled = False
    assert chain.verify(OT.generate(OT.AT_PREFIX), resource=RES).reason == "unknown_token"


def test_oauth_token_on_mcp_and_revocation(live_oauth, oauth_ctx):
    m = mint_tokens(oauth_ctx, scopes=("doc4ai:read",), resource=_res(live_oauth))
    names = {t.name for t in mcp_list_tools(live_oauth, m.access_token).tools}
    assert names == READ_TOOLS
    row = oauth_ctx.store.list_mcp_calls(limit=1)[0]
    assert row["token_id"] == m.grant_id and row["method"] == "tools/list" and row["status"] == "ok"
    from aidoc.oauth.grants import GrantService
    GrantService(oauth_ctx).revoke_grant(m.grant_id, "admin", time.time())
    r = raw_post(live_oauth, m.access_token, LIST)
    assert r.status_code == 401 and r.json()["error"] == "invalid_token"
    www = _www(r)
    assert 'error="invalid_token"' in www and 'error_description="This token was revoked"' in www
    assert f'resource_metadata="{live_oauth}/.well-known/oauth-protected-resource/mcp"' in www
    assert 'scope="doc4ai:read doc4ai:convert"' in www
    assert oauth_ctx.store.list_mcp_calls(limit=1)[0]["token_id"] == m.grant_id                # a known grant is attributed


def test_401_missing_token_has_no_error_code(live_oauth):
    r = raw_post(live_oauth, None, LIST)
    www = _www(r)
    assert r.status_code == 401 and www.startswith('Bearer realm="doc4ai", resource_metadata="')
    assert "error=" not in www and "error_description" not in www and 'scope="doc4ai:read doc4ai:convert"' in www
    assert www == (f'Bearer realm="doc4ai", resource_metadata="{live_oauth}/.well-known/oauth-protected-resource/mcp", '
                   f'scope="doc4ai:read doc4ai:convert"')


def test_401_shape_with_prm(live_oauth):
    r = raw_post(live_oauth, "doc4ai_at_" + "x" * 50, LIST)
    www = _www(r)
    assert set(re.findall(r'(\w+)="', www)) == {"realm", "error", "error_description", "resource_metadata", "scope"}
    assert "exist" not in www.lower()


def test_401_shape_without_oauth_is_unchanged(live_oauth, oauth_ctx):
    """oauth.enabled = false: the Phase 1 header, byte for byte (D9)."""
    oauth_ctx.config.oauth.enabled = False
    r = raw_post(live_oauth, None, LIST)
    assert _www(r) == 'Bearer realm="doc4ai", error="invalid_token", error_description="Authentication required: send ' \
                      'Authorization: Bearer <token>"'
    r = raw_post(live_oauth, "doc4ai_pat_" + "x" * 30, LIST)
    assert _www(r) == 'Bearer realm="doc4ai", error="invalid_token", error_description="The token is not a Doc4AI Studio ' \
                      'token (doc4ai_pat_...)"'
    for p in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-authorization-server"):
        assert httpx.get(live_oauth + p).status_code == 404


def test_at_bound_to_host(live_oauth, oauth_ctx):
    m = mint_tokens(oauth_ctx, resource=_res(live_oauth))
    assert raw_post(live_oauth, m.access_token, LIST).status_code == 200
    other = live_oauth.replace("127.0.0.1", "localhost")
    r = raw_post(other, m.access_token, LIST)
    assert r.status_code == 401 and "another URL of this server" in _www(r)
    assert oauth_ctx.store.list_mcp_calls(limit=1)[0]["error_code"] == "wrong_audience"
    assert f'resource_metadata="{other}/.well-known/oauth-protected-resource/mcp"' in _www(r)   # the PRM of the URL it used


def test_unknown_host_gets_no_resource_metadata(live_oauth):
    r = raw_post(live_oauth, None, LIST, extra_headers={"Host": "evil.test"})
    assert r.status_code == 401 and "resource_metadata" not in _www(r) and "evil" not in _www(r)


def test_forbidden_scope_carries_www_authenticate_meta_for_oauth(live_oauth, oauth_ctx):
    m = mint_tokens(oauth_ctx, scopes=("doc4ai:read",), resource=_res(live_oauth))
    res = mcp_call(live_oauth, m.access_token, "convert_document", {"filename": "a.pdf", "content_base64": "JVBERi0="})
    assert res.is_error and res.structured_content["code"] == "forbidden_scope"
    meta = res.meta["mcp/www_authenticate"]
    assert meta == [(f'Bearer error="insufficient_scope", scope="doc4ai:convert", '
                    f'resource_metadata="{live_oauth}/.well-known/oauth-protected-resource/mcp"')]


def test_forbidden_scope_for_pat_has_no_meta(live_oauth, oauth_ctx):
    secret = oauth_ctx.extras["mcp"].secret
    raw = PT.generate_token()
    oauth_ctx.store.create_api_token(name="p", prefix=PT.display_prefix(raw), token_hash=PT.token_hash(secret, raw),
                                     scopes=["doc4ai:read"], expires_at=None)
    res = mcp_call(live_oauth, raw, "convert_document", {"filename": "a.pdf", "content_base64": "JVBERi0="})
    assert res.is_error and res.structured_content["code"] == "forbidden_scope"
    assert not (res.meta or {}).get("mcp/www_authenticate")


def test_disable_kills_tokens(live_oauth, oauth_ctx):
    m = mint_tokens(oauth_ctx, resource=_res(live_oauth))
    assert raw_post(live_oauth, m.access_token, LIST).status_code == 200
    oauth_ctx.config.oauth.enabled = False
    r = raw_post(live_oauth, m.access_token, LIST)
    assert r.status_code == 401 and "resource_metadata" not in _www(r)


def test_compat_accepts_oauth_token(oauth_client, oauth_ctx):
    m = mint_tokens(oauth_ctx, scopes=("doc4ai:read", "doc4ai:convert"))
    r = oauth_client.get("/v1/status/poll/nope", headers={"Authorization": f"Bearer {m.access_token}"})
    assert r.status_code == 404, r.text                                              # authenticated; the task is unknown
    r = oauth_client.get("/v1/status/poll/nope", headers={"X-Api-Key": m.access_token})
    assert r.status_code == 404
    r = oauth_client.get("/v1/status/poll/nope")
    assert r.status_code == 401 and _www(r).startswith('Bearer realm="doc4ai", resource_metadata="http://127.0.0.1:8765/')
    r = oauth_client.get("/v1/status/poll/nope", headers={"X-Api-Key": m.access_token, "Host": "localhost:8765"})
    assert r.status_code == 401 and "another URL" in r.json()["detail"]
