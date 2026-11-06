"""Spec 2026-11-05 §8 security review: one test (or more) per row. Rows covered by a test written in an earlier task
are imported by name, so this file is the single checklist (each row's tests run here)."""
import logging
import re
import sqlite3
import time

import httpx

from aidoc.oauth.grants import GrantService
from tests.mcp.conftest import raw_post
from tests.oauth.conftest import (
    CB_DEFAULT,
    ISSUER,
    LIST,
    auth_params,
    consent_post,
    exchange,
    location_query,
    mint_tokens,
    pkce,
    register_client,
)

# row: code interception / replay
# row: SDK end to end
from tests.oauth.test_authorize import (  # noqa: F401
    test_bad_redirect_or_client_never_redirects,  # row: redirect manipulation / open redirect
    test_consent_csrf_rejected_and_headers,  # row: consent CSRF / clickjacking / auto-consent
    test_consent_lockout_refuses_even_the_right_token,  # row: server token brute force
    test_consent_nonce_burned_after_wrong_guesses,
    test_consent_nonce_single_use,
    test_consent_shows_host_and_redirect,  # row: CIMD impersonation / localhost risk
    test_get_never_issues_code,
    test_token_mode_requires_server_token,
    test_unknown_host_never_becomes_issuer,  # row: loopback mode without credentials, Host restricted
)
from tests.oauth.test_cimd_ssrf import (  # noqa: F401  row: CIMD SSRF (network layer)
    local_listener,
    test_embedded_and_special_addresses_blocked,
    test_rebinding_second_lookup_is_never_dialed,
)
from tests.oauth.test_clients import (  # noqa: F401
    test_cimd_blocks_private_and_redirect,  # row: CIMD SSRF
    test_cimd_fetch_rules,
    test_dcr_redirect_rules,
    test_dcr_register_and_limits,  # row: DCR flood
    test_redirect_matching_rules,
)
from tests.oauth.test_sdk_e2e import (  # noqa: F401
    test_sdk_oauth_flow_cimd,
    test_sdk_oauth_flow_dcr,
    test_sdk_refresh_and_revoke,
)
from tests.oauth.test_store_v8 import test_prune_oauth  # noqa: F401  row: DCR flood (unused clients pruned)
from tests.oauth.test_token_endpoint import test_code_reuse_revokes_grant  # noqa: F401
from tests.oauth.test_tokens_redaction import test_redaction_new_prefixes  # noqa: F401  row: secrets in logs
from tests.oauth.test_verifier_gate import (  # noqa: F401
    test_401_missing_token_has_no_error_code,  # row: 401 leaks
    test_401_shape_with_prm,
    test_401_shape_without_oauth_is_unchanged,  # row: PAT regression
    test_at_bound_to_host,  # row: token used at another URL
    test_disable_kills_tokens,  # row: OAuth disabled leaves nothing usable
)


def test_plain_pkce_rejected(oauth_client, dcr_client):
    for method in ("plain", None):
        r = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client, code_challenge_method=method), follow_redirects=False)
        assert r.status_code == 302 and location_query(r)["error"] == "invalid_request"
    assert oauth_client.get("/.well-known/oauth-authorization-server").json()["code_challenge_methods_supported"] == ["S256"]


def test_code_expires(oauth_client, oauth_ctx, dcr_client):
    v, c = pkce()
    html = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client, c)).text
    code = location_query(consent_post(oauth_client, html))["code"]
    row = oauth_ctx.store._q1("SELECT * FROM oauth_codes")
    assert row["expires_at"] - row["created_at"] == 300
    oauth_ctx.store.con.execute("UPDATE oauth_codes SET expires_at=?", (time.time() - 1,))
    r = exchange(oauth_client, dcr_client, code, v)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_iss_in_response(oauth_client, dcr_client):
    html = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client)).text
    assert location_query(consent_post(oauth_client, html))["iss"] == ISSUER                          # success
    html = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client)).text
    assert location_query(consent_post(oauth_client, html, decision="deny"))["iss"] == ISSUER         # denied
    r = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client, response_type="token"), follow_redirects=False)
    assert location_query(r)["iss"] == ISSUER                                                         # parameter error


def test_wrong_resource_invalid_target(oauth_client, dcr_client):
    r = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client, resource="http://127.0.0.1:8765/other"),
                         follow_redirects=False)
    assert location_query(r)["error"] == "invalid_target"
    v, c = pkce()
    html = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client, c)).text
    code = location_query(consent_post(oauth_client, html))["code"]
    r = exchange(oauth_client, dcr_client, code, v, resource="http://localhost:8765/mcp")
    assert r.status_code == 400 and r.json()["error"] == "invalid_target"


def test_refresh_rotation_and_replay(oauth_client, oauth_ctx, dcr_client):
    m = mint_tokens(oauth_ctx, client_id=dcr_client["client_id"])
    form = {"grant_type": "refresh_token", "refresh_token": m.refresh_token, "client_id": m.client_id}
    first = oauth_client.post("/oauth/token", data=form)
    assert first.status_code == 200 and first.json()["refresh_token"] != m.refresh_token
    replay = oauth_client.post("/oauth/token", data=form)
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    assert oauth_ctx.store.get_oauth_grant(m.grant_id)["revoked_reason"] == "refresh_replay"
    nxt = oauth_client.post("/oauth/token", data={**form, "refresh_token": first.json()["refresh_token"]})
    assert nxt.status_code == 400                                                       # the successor dies with the grant


def _full_flow(client, ctx):
    """register → consent → code → token → refresh: every secret this server mints, in one place."""
    row = register_client(client)
    v, c = pkce()
    html = client.get("/oauth/authorize", params=auth_params(row, c)).text
    code = location_query(consent_post(client, html))["code"]
    t = exchange(client, row, code, v).json()
    t2 = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"],
                                           "client_id": row["client_id"]}).json()
    return {"code": code, "verifier": v, "at": t["access_token"], "rt": t["refresh_token"], "at2": t2["access_token"],
            "rt2": t2["refresh_token"]}


def test_tokens_stored_hashed_only(oauth_client, oauth_ctx):
    secrets_ = _full_flow(oauth_client, oauth_ctx)
    con = sqlite3.connect(str(oauth_ctx.store.db_path))
    dump = []
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        for row in con.execute(f'SELECT * FROM "{table}"'):
            dump.append(repr(row))
    con.close()
    blob = "\n".join(dump)
    for name, raw in secrets_.items():
        assert raw not in blob, name
        body = raw.split("_", 2)[2] if raw.startswith("doc4ai_") else raw     # the secret part (a verifier has no prefix)
        assert len(body) >= 43 and body not in blob, name                         # not even without the prefix
    # only display prefixes (prefix + 4 characters) ever appear
    assert not re.search(r"doc4ai_(at|rt|ac)_[0-9A-Za-z]{5,}", blob)


def test_revoke_kills_access_and_refresh(live_oauth, oauth_ctx):
    """Admin revoke, /oauth/revoke, refresh replay, code reuse: each marks every AT and RT of the grant, and the next
    /mcp request with its access token is 401."""
    from fastapi.testclient import TestClient

    from aidoc.server.app import create_app
    res = live_oauth + "/mcp"
    svc = GrantService(oauth_ctx)

    def all_revoked(gid):
        rows = oauth_ctx.store._qa("SELECT * FROM oauth_tokens WHERE grant_id=?", (gid,))
        return rows and all(r["revoked_at"] is not None for r in rows)

    with TestClient(create_app(oauth_ctx), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 1)) as admin:
        m = mint_tokens(oauth_ctx, resource=res)
        assert raw_post(live_oauth, m.access_token, LIST).status_code == 200
        assert admin.post(f"/api/mcp/tokens/{m.grant_id}/revoke", json={}).status_code == 200
        assert all_revoked(m.grant_id) and raw_post(live_oauth, m.access_token, LIST).status_code == 401

        m = mint_tokens(oauth_ctx, resource=res)
        assert httpx.post(live_oauth + "/oauth/revoke", data={"token": m.refresh_token}).status_code == 200
        assert all_revoked(m.grant_id) and raw_post(live_oauth, m.access_token, LIST).status_code == 401

    m = mint_tokens(oauth_ctx, resource=res)
    svc.refresh({"grant_type": "refresh_token", "refresh_token": m.refresh_token, "client_id": m.client_id}, "127.0.0.1", time.time())
    try:
        svc.refresh({"grant_type": "refresh_token", "refresh_token": m.refresh_token, "client_id": m.client_id}, "127.0.0.1", time.time())
    except Exception:  # noqa: BLE001, S110  invalid_grant is the point
        pass
    assert all_revoked(m.grant_id) and raw_post(live_oauth, m.access_token, LIST).status_code == 401

    row = oauth_ctx.store.get_oauth_client(m.client_id)
    v, c = pkce()
    code = svc.issue_code(row, CB_DEFAULT, c, ["doc4ai:read"], res, "127.0.0.1", time.time())
    form = {"grant_type": "authorization_code", "code": code, "code_verifier": v, "redirect_uri": CB_DEFAULT, "client_id": m.client_id}
    tr = svc.exchange_code(form, "127.0.0.1", time.time())
    assert raw_post(live_oauth, tr.access_token, LIST).status_code == 200
    try:
        svc.exchange_code(form, "127.0.0.1", time.time())
    except Exception:  # noqa: BLE001, S110
        pass
    assert all_revoked(tr.grant_id) and raw_post(live_oauth, tr.access_token, LIST).status_code == 401


def test_query_token_rejected_on_mcp(live_oauth, oauth_ctx):
    m = mint_tokens(oauth_ctx, resource=live_oauth + "/mcp")
    r = httpx.post(f"{live_oauth}/mcp?access_token={m.access_token}", json=LIST,
                   headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/list"})
    assert r.status_code == 400 and r.json()["error"] == "token_in_query"
    assert m.access_token not in str(oauth_ctx.store.list_mcp_calls(limit=5))


def test_consent_headers(oauth_client, dcr_client):
    r = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client))
    csp = r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in csp and "default-src 'none'" in csp
    assert "form-action" not in csp and r.headers["referrer-policy"] == "same-origin"
    assert oauth_client.get("/oauth/authorize", params=auth_params(dcr_client, redirect_uri="http://evil/cb")).headers["x-frame-options"] == "DENY"


def test_oauth_secrets_never_logged(oauth_client, oauth_ctx, caplog):
    from aidoc.server.logfilter import install_access_log_redaction
    install_access_log_redaction()
    with caplog.at_level(logging.DEBUG):
        s = _full_flow(oauth_client, oauth_ctx)
        # what uvicorn's access log would print for the callback and a stray query-string token request
        access = logging.getLogger("uvicorn.access")
        access.addHandler(caplog.handler)              # uvicorn's access logger does not propagate to the root
        access.setLevel(logging.INFO)
        access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5", "GET", f"/callback?code={s['code']}&state=st4te", "1.1", 200)
        access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5", "POST",
                    f"/oauth/token?code={s['code']}&code_verifier={s['verifier']}&refresh_token={s['rt']}", "1.1", 400)
        access.removeHandler(caplog.handler)
    blob = (caplog.text + "".join(r.getMessage() for r in caplog.records) + repr(oauth_ctx.store.list_mcp_calls(limit=100))
            + repr(oauth_ctx.store.events_since(0)))
    for name, raw in s.items():
        assert raw not in blob, name
    access_lines = list(dict.fromkeys(r.getMessage() for r in caplog.records if r.name == "uvicorn.access"))
    assert len(access_lines) == 2 and "/callback?code=<redacted>&state=st4te" in access_lines[0]      # the lines were seen
    assert "code_verifier=<redacted>" in access_lines[1] and "refresh_token=<redacted>" in access_lines[1]
    authorize = [c for c in oauth_ctx.store.list_mcp_calls(limit=50) if c["method"] == "oauth/authorize"]
    assert authorize and all(s["code"] not in (c["args_summary"] or "") for c in authorize)


def test_checklist_is_complete():
    """Every spec §8 row names at least one test that exists in this module."""
    rows = {
        "授權碼攔截／重放": ["test_plain_pkce_rejected", "test_code_reuse_revokes_grant", "test_code_expires"],
        "redirect 操縱／開放轉址": ["test_redirect_matching_rules", "test_bad_redirect_or_client_never_redirects", "test_dcr_redirect_rules"],
        "mix-up": ["test_iss_in_response"],
        "token 用在別的 URL": ["test_wrong_resource_invalid_target", "test_at_bound_to_host"],
        "refresh 外洩": ["test_refresh_rotation_and_replay", "test_tokens_stored_hashed_only"],
        "CIMD SSRF": ["test_cimd_blocks_private_and_redirect", "test_cimd_fetch_rules", "test_rebinding_second_lookup_is_never_dialed",
                      "test_embedded_and_special_addresses_blocked"],
        "CIMD 假冒／localhost 風險": ["test_consent_shows_host_and_redirect"],
        "DCR 洪水": ["test_dcr_register_and_limits", "test_prune_oauth"],
        "同意頁 CSRF／點擊劫持／自動同意": ["test_consent_csrf_rejected_and_headers", "test_consent_headers", "test_get_never_issues_code",
                                  "test_consent_nonce_single_use"],
        "server token 暴力": ["test_token_mode_requires_server_token", "test_consent_lockout_refuses_even_the_right_token",
                         "test_consent_nonce_burned_after_wrong_guesses"],
        "loopback 模式無憑證但 Host 受限": ["test_unknown_host_never_becomes_issuer"],
        "token 外洩到 log": ["test_oauth_secrets_never_logged", "test_redaction_new_prefixes"],
        "401 洩漏": ["test_401_shape_with_prm", "test_401_missing_token_has_no_error_code"],
        "PAT 迴歸": ["test_401_shape_without_oauth_is_unchanged"],
        "停用 OAuth 後殘留": ["test_disable_kills_tokens"],
        "撤銷即時": ["test_revoke_kills_access_and_refresh"],
        "token 不進 URL": ["test_query_token_rejected_on_mcp"],
        "SDK 端對端": ["test_sdk_oauth_flow_dcr", "test_sdk_oauth_flow_cimd", "test_sdk_refresh_and_revoke"],
    }
    assert len(rows) == 18
    g = globals()
    missing = [t for names in rows.values() for t in names if not callable(g.get(t))]
    assert missing == []
