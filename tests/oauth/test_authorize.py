"""/oauth/authorize (spec 2026-11-05 §5.3 (1)–(3), §6): the 授權連線 page, its CSRF-safe POST, token-mode credential, iss."""
import re

import pytest

from aidoc.oauth.consent import TEXT
from tests.oauth.conftest import (
    CLAUDE_CODE_CIMD,
    ISSUER,
    RES,
    SERVER_TOKEN,
    auth_params,
    consent_post,
    exchange,
    location_query,
    pkce,
    register_client,
)

CB = "http://127.0.0.1:3118/callback"


def _codes(ctx):
    return ctx.store._q1("SELECT count(*) AS n FROM oauth_codes")["n"]


def test_consent_page_dcr_loopback(oauth_client, dcr_client):
    r = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client, scope="doc4ai:read doc4ai:convert doc4ai:manage"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html"), r.text
    html = r.text
    assert TEXT["title"] in html and "Test IDE" in html and "未驗證" in html and CB in html and TEXT["loopback_note"] in html
    assert f"本機註冊的 client（{dcr_client['client_id']}）" in html and ISSUER in html
    assert 'name="request" value="' in html and 'type="password"' not in html and TEXT["loopback_only"] in html
    assert re.search(r'value="doc4ai:read"[^>]*checked', html) and re.search(r'value="doc4ai:convert"[^>]*checked', html)
    assert not re.search(r'value="doc4ai:manage"[^>]*checked', html) and html.count(TEXT["high_risk"]) == 1
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "form-action" not in r.headers["content-security-policy"] and r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store" and "<script" not in html


def test_consent_shows_host_and_redirect(oauth_client, fake_cimd):
    fake_cimd.put(CLAUDE_CODE_CIMD["client_id"], CLAUDE_CODE_CIMD)
    row = {"client_id": CLAUDE_CODE_CIMD["client_id"]}
    r = oauth_client.get("/oauth/authorize", params=auth_params(row, redirect_uri="http://localhost:3118/callback", scope=None))
    assert r.status_code == 200, r.text
    assert "<strong>claude.ai</strong>" in r.text and "Claude Code" in r.text and "未驗證" in r.text
    assert "http://localhost:3118/callback" in r.text
    assert re.search(r'value="doc4ai:read"[^>]*checked', r.text) and 'value="doc4ai:manage"' not in r.text   # default scope


def test_bad_redirect_or_client_never_redirects(oauth_client, dcr_client, fake_cimd):
    cases = [({"redirect_uri": "http://evil.example/cb"}, TEXT["err_bad_redirect"]),
             ({"redirect_uri": "http://127.0.0.1:3118/other"}, TEXT["err_bad_redirect"]),
             ({"redirect_uri": None}, TEXT["err_bad_redirect"]),
             ({"client_id": "doc4ai_cl_nobody"}, TEXT["err_unknown_client"]),
             ({"client_id": None}, TEXT["err_unknown_client"]),
             ({"client_id": "https://nobody.test/m"}, "無法取得 client 的描述文件"),
             ({"state": None}, TEXT["err_missing_state"])]
    for over, text in cases:
        r = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client, **over), follow_redirects=False)
        assert r.status_code == 400 and "location" not in r.headers and text in r.text, (over, r.text)
        assert r.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("over,error", [
    ({"code_challenge_method": "plain"}, "invalid_request"), ({"code_challenge_method": None}, "invalid_request"),
    ({"code_challenge": None}, "invalid_request"), ({"code_challenge": "short"}, "invalid_request"),
    ({"resource": "http://127.0.0.1:8765/other"}, "invalid_target"), ({"resource": "http://localhost:8765/mcp"}, "invalid_target"),
    ({"response_type": "token"}, "unsupported_response_type"), ({"response_type": None}, "unsupported_response_type"),
    ({"scope": "doc4ai:read offline_access"}, "invalid_scope"),
])
def test_param_errors_redirect_with_error(oauth_client, dcr_client, over, error):
    r = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client, **over), follow_redirects=False)
    assert r.status_code == 302, r.text
    assert r.headers["location"].startswith(CB + "?")
    q = location_query(r)
    assert q["error"] == error and q["state"] == "st4te" and q["iss"] == ISSUER and q.get("error_description")
    assert "code" not in q


def test_resource_omitted_is_consent(oauth_client, dcr_client):
    r = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client, resource=None))
    assert r.status_code == 200 and 'name="request"' in r.text


def test_consent_allow_redirects_with_code_and_iss(oauth_client, oauth_ctx, dcr_client):
    v, c = pkce()
    html = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client, c)).text
    r = consent_post(oauth_client, html)
    assert r.status_code == 302 and r.headers["location"].startswith(CB + "?")
    q = location_query(r)
    assert q["state"] == "st4te" and q["iss"] == ISSUER and q["code"].startswith("doc4ai_ac_") and set(q) == {"code", "state", "iss"}
    assert r.headers["referrer-policy"] == "no-referrer" and r.headers["cache-control"] == "no-store"
    t = exchange(oauth_client, dcr_client, q["code"], v, resource=RES)
    assert t.status_code == 200 and t.json()["scope"] == "doc4ai:read doc4ai:convert", t.text
    row = next(cl for cl in oauth_ctx.store.list_mcp_calls(limit=10) if cl["method"] == "oauth/authorize")
    assert row["status"] == "ok" and row["http_status"] == 302
    assert '"client_host"' in row["args_summary"] and '"redirect_host": "127.0.0.1"' in row["args_summary"]
    assert q["code"] not in str(row)


def test_consent_scope_subset_and_deny(oauth_client, oauth_ctx, dcr_client):
    v, c = pkce()
    params = auth_params(dcr_client, c, scope="doc4ai:read doc4ai:convert doc4ai:manage")
    r = consent_post(oauth_client, oauth_client.get("/oauth/authorize", params=params).text, scopes=["doc4ai:read", "doc4ai:manage"])
    t = exchange(oauth_client, dcr_client, location_query(r)["code"], v)
    assert t.json()["scope"] == "doc4ai:read doc4ai:manage"
    r = consent_post(oauth_client, oauth_client.get("/oauth/authorize", params=params).text, decision="deny")
    q = location_query(r)
    assert r.status_code == 302 and q["error"] == "access_denied" and q["state"] == "st4te" and q["iss"] == ISSUER and "code" not in q
    for bad in ([], ["doc4ai:convert"], ["doc4ai:read", "doc4ai:convert:local"], ["doc4ai:read", "bogus"]):
        r = consent_post(oauth_client, oauth_client.get("/oauth/authorize", params=params).text, scopes=bad)
        assert r.status_code == 400 and TEXT["err_scopes"] in r.text, bad
    r = consent_post(oauth_client, oauth_client.get("/oauth/authorize", params=params).text, decision="maybe")
    assert r.status_code == 400


def test_consent_csrf_rejected_and_headers(oauth_client, oauth_ctx, dcr_client):
    html = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client)).text
    assert consent_post(oauth_client, html, request="forged.blob").status_code == 400
    r = consent_post(oauth_client, html, headers={"Origin": "http://evil.test"})
    assert r.status_code == 403 and "location" not in r.headers
    r = consent_post(oauth_client, html, headers={"Origin": ISSUER, "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    r = consent_post(oauth_client, html, headers={"Origin": "null"})
    assert r.status_code == 403
    r = consent_post(oauth_client, html, headers={"Origin": "http://localhost:8765"})       # another loopback name: not same-origin
    assert r.status_code == 403
    assert _codes(oauth_ctx) == 0
    assert consent_post(oauth_client, html, headers={}).status_code == 302                    # no browser headers: non-browser client
    assert consent_post(oauth_client, html, headers={"Sec-Fetch-Site": "none"}).status_code == 400   # nonce already used


def test_get_never_issues_code(oauth_client, oauth_ctx, dcr_client):
    for _ in range(3):
        oauth_client.get("/oauth/authorize", params=auth_params(dcr_client))
    oauth_client.get("/oauth/authorize", params={**auth_params(dcr_client), "decision": "allow", "scopes": "doc4ai:read"})
    assert _codes(oauth_ctx) == 0


def test_consent_nonce_single_use(oauth_client, dcr_client):
    html = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client)).text
    assert consent_post(oauth_client, html).status_code == 302
    r = consent_post(oauth_client, html)
    assert r.status_code == 400 and TEXT["err_used"] in r.text
    html = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client)).text
    assert consent_post(oauth_client, html, decision="deny").status_code == 302
    assert consent_post(oauth_client, html).status_code == 400                               # a denied page is used too


def test_consent_request_expires(oauth_client, oauth_ctx, dcr_client, monkeypatch):
    html = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client)).text
    import aidoc.oauth.endpoints as E
    real = E.time.time
    monkeypatch.setattr(E.time, "time", lambda: real() + oauth_ctx.config.oauth.consent_ttl_s + 1)
    r = consent_post(oauth_client, html)
    assert r.status_code == 400 and TEXT["err_expired"] in r.text


def test_signed_request_bound_to_host(oauth_client, dcr_client):
    """A page served for localhost cannot be submitted to 127.0.0.1 (its resource names the other issuer)."""
    html = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client, resource=None), headers={"Host": "localhost:8765"}).text
    r = consent_post(oauth_client, html, headers={})
    assert r.status_code == 400


def test_token_mode_requires_server_token(token_oauth_client, token_oauth_ctx):
    row = register_client(token_oauth_client)
    html = token_oauth_client.get("/oauth/authorize", params=auth_params(row)).text
    assert 'type="password" name="server_token"' in html and TEXT["loopback_only"] not in html
    assert consent_post(token_oauth_client, html).status_code == 401                         # no token
    r = consent_post(token_oauth_client, html, server_token="wrong")
    assert r.status_code == 401 and TEXT["err_wrong_token"] in r.text
    assert consent_post(token_oauth_client, html, server_token=SERVER_TOKEN).status_code == 302   # the same page still works
    assert consent_post(token_oauth_client, html, server_token=SERVER_TOKEN).status_code == 400   # ...once
    html = token_oauth_client.get("/oauth/authorize", params=auth_params(row)).text
    assert consent_post(token_oauth_client, html, server_token=SERVER_TOKEN, decision="deny").status_code == 302


def test_consent_bruteforce_429_and_logged(token_oauth_client, token_oauth_ctx):
    row = register_client(token_oauth_client)
    html = token_oauth_client.get("/oauth/authorize", params=auth_params(row)).text
    for _ in range(10):
        assert consent_post(token_oauth_client, html, server_token="wrong").status_code == 401
    r = consent_post(token_oauth_client, html, server_token="wrong")
    assert r.status_code == 429 and "retry-after" in r.headers and TEXT["err_too_many"] in r.text
    rows = [c for c in token_oauth_ctx.store.list_mcp_calls(limit=50) if c["method"] == "oauth/authorize"]
    assert len([c for c in rows if c["status"] == "auth_error" and c["error_code"] == "consent_token"]) == 10
    assert all("wrong" not in str(c) for c in rows)


def test_unknown_host_never_becomes_issuer(oauth_client, dcr_client):
    r = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client), headers={"Host": "evil.test"}, follow_redirects=False)
    assert r.status_code in (400, 403) and "location" not in r.headers and "evil" not in r.text
    r = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client, response_type="token"), headers={"Host": "evil.test"},
                         follow_redirects=False)
    assert r.status_code in (400, 403) and "location" not in r.headers


def test_authorize_get_rate_limited(oauth_client, dcr_client):
    params = auth_params(dcr_client)
    for _ in range(60):
        assert oauth_client.get("/oauth/authorize", params=params).status_code == 200
    r = oauth_client.get("/oauth/authorize", params=params)
    assert r.status_code == 429 and "retry-after" in r.headers


def test_private_scheme_redirect_shows_warning(oauth_client):
    row = register_client(oauth_client, redirect_uris=("cursor://anysphere.cursor-mcp/oauth/callback",), name="Cursor")
    params = auth_params(row, redirect_uri="cursor://anysphere.cursor-mcp/oauth/callback")
    html = oauth_client.get("/oauth/authorize", params=params).text
    assert TEXT["private_note"] in html and "cursor://anysphere.cursor-mcp/oauth/callback" in html
    r = consent_post(oauth_client, html)
    assert r.status_code == 302 and r.headers["location"].startswith("cursor://anysphere.cursor-mcp/oauth/callback?code=doc4ai_ac_")


def test_disabled_is_404(oauth_client, oauth_ctx, dcr_client):
    html = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client)).text
    oauth_ctx.config.oauth.enabled = False
    assert oauth_client.get("/oauth/authorize", params=auth_params(dcr_client)).status_code == 404
    assert consent_post(oauth_client, html).status_code == 404
