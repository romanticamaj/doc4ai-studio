"""Private-use redirect schemes and pre-consent error redirects (verifier F3 + F6, coordinator review 2026-11-06).

Rule: a private-use redirect URI is accepted only when its scheme is reverse-domain style (contains a dot, RFC 8252
§7.1) or is a verified native-client scheme (NATIVE_SCHEMES), never a browser or system handler, and its
scheme-specific part never embeds another URL. Before consent, errors are redirected only to loopback redirect URIs;
any other kind gets the local error page unless the client already holds a live grant."""
import time

import pytest

from aidoc.oauth import clients
from aidoc.oauth.clients import ClientError, ClientRegistry, classify_redirect
from aidoc.oauth.consent import TEXT
from tests.oauth.conftest import (
    auth_params,
    consent_post,
    exchange,
    location_query,
    mint_tokens,
    pkce,
    register_client,
)

CURSOR_CB = "cursor://anysphere.cursor-mcp/oauth/callback"


@pytest.mark.parametrize("uri", [
    CURSOR_CB,                                            # Cursor's real DCR redirect (spec §2 source)
    "com.example.app:/oauth2redirect",                   # RFC 8252 §7.1 reverse-domain
    "com.example.app://host:8080/cb",
    "io.github.someone.tool:/cb",
])
def test_private_schemes_allowed(uri):
    assert classify_redirect(uri) == "private"


@pytest.mark.parametrize("uri", [
    "microsoft-edge:https://evil.example/x", "microsoft-edge:http://evil.example", "ms-msdt:/id PCWDiagnostic",
    "ms-settings:privacy", "ms-word:ofe|u|https://evil.example/a.docx", "search-ms:query=x", "intent://evil#Intent;end",
    "view-source:https://evil.example", "file:///C:/Windows", "javascript:alert(1)", "data:text/html,x", "vbscript:x",
    "shell:startup", "chrome://settings", "edge://settings", "mailto:a@b.c", "tel:1",
    "myapp://cb",                                         # no dot and not a verified native client
    "com.example.app:https://evil.example/x",             # reverse-domain, but it embeds another URL
    "com.example.app://https://evil.example",
    "com.example.app:javascript:alert(1)",
    "ms-office.app:/cb", "microsoft.edge:/cb",            # a dot does not make a system handler acceptable
])
def test_dangerous_or_unknown_schemes_refused(uri):
    assert classify_redirect(uri) is None


def test_cimd_document_with_a_handler_redirect_is_refused(fake_cimd, oauth_ctx):
    url = "https://evil.example/cimd.json"
    fake_cimd.put(url, {"client_id": url, "client_name": "x", "redirect_uris": ["http://127.0.0.1/cb", "microsoft-edge:https://evil.example/x"]})
    with pytest.raises(ClientError, match="redirect_uris"):
        ClientRegistry(oauth_ctx).resolve(url, now=time.time())


def test_dcr_with_a_handler_redirect_is_refused(oauth_client):
    r = oauth_client.post("/oauth/register", json={"redirect_uris": ["microsoft-edge:https://evil.example/x"]})
    assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"


def test_pre_consent_error_for_a_private_scheme_client_renders_locally(oauth_client):
    row = register_client(oauth_client, redirect_uris=(CURSOR_CB,), name="Cursor")
    r = oauth_client.get("/oauth/authorize", params=auth_params(row, redirect_uri=CURSOR_CB, response_type="token"),
                         follow_redirects=False)
    assert r.status_code == 400 and "location" not in r.headers and TEXT["error_title"] in r.text


def test_pre_consent_error_redirects_once_the_client_has_a_live_grant(oauth_client, oauth_ctx):
    row = register_client(oauth_client, redirect_uris=(CURSOR_CB,), name="Cursor")
    m = mint_tokens(oauth_ctx, client_id=row["client_id"], redirect=CURSOR_CB)
    r = oauth_client.get("/oauth/authorize", params=auth_params(row, redirect_uri=CURSOR_CB, response_type="token"),
                         follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith(CURSOR_CB + "?error=unsupported_response_type")
    from aidoc.oauth.grants import GrantService
    GrantService(oauth_ctx).revoke_grant(m.grant_id, "admin", time.time())             # no live grant any more
    r = oauth_client.get("/oauth/authorize", params=auth_params(row, redirect_uri=CURSOR_CB, response_type="token"),
                         follow_redirects=False)
    assert r.status_code == 400 and "location" not in r.headers


def test_loopback_errors_still_redirect(oauth_client):
    row = register_client(oauth_client)
    r = oauth_client.get("/oauth/authorize", params=auth_params(row, response_type="token"), follow_redirects=False)
    assert r.status_code == 302 and location_query(r)["error"] == "unsupported_response_type"


def test_cursor_real_payload_registers_and_completes_the_flow(oauth_client):
    body = {"client_name": "Cursor", "redirect_uris": [CURSOR_CB, "https://www.cursor.com/agents/mcp/oauth/callback",
            "http://localhost:8787/callback"], "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none"}
    reg = oauth_client.post("/oauth/register", json=body)
    assert reg.status_code == 201, reg.text
    row = reg.json()
    v, c = pkce()
    html = oauth_client.get("/oauth/authorize", params=auth_params(row, c, redirect_uri=CURSOR_CB)).text
    r = consent_post(oauth_client, html)
    assert r.status_code == 302 and r.headers["location"].startswith(CURSOR_CB + "?code=doc4ai_ac_")
    t = exchange(oauth_client, row, location_query(r)["code"], v, redirect=CURSOR_CB)
    assert t.status_code == 200 and t.json()["access_token"].startswith("doc4ai_at_")


def test_native_schemes_are_only_verified_ones():
    assert clients.NATIVE_SCHEMES == frozenset({"cursor"})
