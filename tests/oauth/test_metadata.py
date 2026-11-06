"""Discovery (spec 2026-11-05 §4.1–§4.4): PRM and AS metadata derived from the Host the client used."""
from tests.oauth.conftest import ISSUER, RES

ALL_SCOPES = ["doc4ai:read", "doc4ai:convert", "doc4ai:convert:local", "doc4ai:manage"]
WELL_KNOWN = ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource",
              "/.well-known/oauth-authorization-server", "/.well-known/openid-configuration")


def test_prm_and_as_metadata_from_host(oauth_client):
    prm = oauth_client.get("/.well-known/oauth-protected-resource/mcp")
    assert prm.status_code == 200 and "workspace" not in prm.json() and prm.headers["cache-control"] == "public, max-age=300"
    assert prm.headers["x-content-type-options"] == "nosniff"
    assert prm.json() == {"resource": RES, "authorization_servers": [ISSUER], "scopes_supported": ALL_SCOPES,
                          "bearer_methods_supported": ["header"], "resource_name": "Doc4AI Studio",
                          "resource_documentation": ISSUER + "/mcp-admin"}
    assert oauth_client.get("/.well-known/oauth-protected-resource").json() == prm.json()
    m = oauth_client.get("/.well-known/oauth-authorization-server").json()
    assert m["issuer"] == ISSUER and m["authorization_endpoint"] == ISSUER + "/oauth/authorize"
    assert m["token_endpoint"] == ISSUER + "/oauth/token"
    assert m["registration_endpoint"] == ISSUER + "/oauth/register" and m["revocation_endpoint"] == ISSUER + "/oauth/revoke"
    assert m["code_challenge_methods_supported"] == ["S256"] and m["token_endpoint_auth_methods_supported"] == ["none"]
    assert m["revocation_endpoint_auth_methods_supported"] == ["none"] and m["response_types_supported"] == ["code"]
    assert m["response_modes_supported"] == ["query"] and m["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert m["client_id_metadata_document_supported"] is True and m["authorization_response_iss_parameter_supported"] is True
    assert m["resource_indicators_supported"] is True and m["service_documentation"] == ISSUER + "/mcp-admin"
    assert m["scopes_supported"] == ALL_SCOPES and "offline_access" not in m["scopes_supported"] and "workspace" not in m
    assert oauth_client.get("/.well-known/openid-configuration").json() == m


def test_issuer_follows_the_host_the_client_used(oauth_client):
    m = oauth_client.get("/.well-known/oauth-authorization-server", headers={"Host": "localhost:8765"}).json()
    assert m["issuer"] == "http://localhost:8765" and m["token_endpoint"] == "http://localhost:8765/oauth/token"
    assert oauth_client.get("/.well-known/oauth-authorization-server", headers={"Host": "[::1]:8765"}).json()["issuer"] == \
        "http://[::1]:8765"
    assert oauth_client.get("/.well-known/oauth-authorization-server", headers={"Host": "LOCALHOST:8765"}).json()["issuer"] == \
        "http://localhost:8765"
    for bad in ("evil.test:8765", "evil.test", "127.0.0.1.evil.test:8765", "localhost:abc", "127.0.0.1:8765@evil",
                "[::1", "a:b:c"):
        r = oauth_client.get("/.well-known/oauth-authorization-server", headers={"Host": bad})
        assert r.status_code in (400, 403) and "issuer" not in r.text and "evil" not in r.text, bad      # never an issuer


def test_token_mode_host_must_be_listed(token_oauth_client, token_oauth_ctx):
    r = token_oauth_client.get("/.well-known/oauth-protected-resource/mcp", headers={"Host": "evil.test:8765"})
    assert r.status_code == 400 and r.json() == {"error": "invalid_request", "error_description": "unknown host"}
    token_oauth_ctx.config.mcp.allowed_hosts = ["box.tail1.ts.net"]
    r = token_oauth_client.get("/.well-known/oauth-protected-resource/mcp", headers={"Host": "box.tail1.ts.net:8765"})
    assert r.status_code == 200 and r.json()["resource"] == "http://box.tail1.ts.net:8765/mcp"


def test_flags_hide_endpoints_and_disabled_is_404(oauth_client, oauth_ctx):
    oauth_ctx.config.oauth.allow_dcr = False
    oauth_ctx.config.oauth.allow_cimd = False
    m = oauth_client.get("/.well-known/oauth-authorization-server").json()
    assert "registration_endpoint" not in m and "client_id_metadata_document_supported" not in m
    assert oauth_client.post("/oauth/register", json={}).status_code == 404
    oauth_ctx.config.oauth.enabled = False
    for p in WELL_KNOWN:
        assert oauth_client.get(p).status_code == 404
    oauth_ctx.config.oauth.enabled = True
    assert oauth_client.get(WELL_KNOWN[0]).status_code == 200


def test_unit_helpers(oauth_ctx):
    from aidoc.oauth.metadata import allowed_host, prm_url
    assert allowed_host(oauth_ctx, "127.0.0.1:8765") == "127.0.0.1:8765"
    assert allowed_host(oauth_ctx, "127.0.0.1") is None                 # bound to 8765: a port-less Host is another port
    assert allowed_host(oauth_ctx, "Localhost:8765") == "localhost:8765"
    assert allowed_host(oauth_ctx, None) is None and allowed_host(oauth_ctx, "") is None
    assert allowed_host(oauth_ctx, "evil.test") is None and allowed_host(oauth_ctx, "::1") is None
    assert prm_url(ISSUER) == ISSUER + "/.well-known/oauth-protected-resource/mcp"


def test_issuer_port_must_be_the_bound_one(oauth_client, oauth_ctx):
    """Verifier F5: only the port this server is bound to (or none, when that is the scheme default) becomes part of
    an issuer; any other port in Host is an unknown host."""
    for host in ("127.0.0.1:9999", "localhost:1", "127.0.0.1", "[::1]:80", "127.0.0.1:08765"):
        r = oauth_client.get("/.well-known/oauth-authorization-server", headers={"Host": host})
        assert r.status_code == 400 and "issuer" not in r.text, host
    assert oauth_client.get("/.well-known/oauth-authorization-server", headers={"Host": "127.0.0.1:8765"}).json()["issuer"] == ISSUER
    oauth_ctx.extras["bind_port"] = 80
    assert oauth_client.get("/.well-known/oauth-authorization-server", headers={"Host": "127.0.0.1"}).json()["issuer"] == \
        "http://127.0.0.1"
    assert oauth_client.get("/.well-known/oauth-authorization-server", headers={"Host": "127.0.0.1:80"}).status_code == 200
    assert oauth_client.get("/.well-known/oauth-authorization-server", headers={"Host": "127.0.0.1:8765"}).status_code == 400


def test_mcp_401_has_no_resource_metadata_for_another_port(live_oauth):
    from tests.mcp.conftest import raw_post
    from tests.oauth.conftest import LIST
    port = int(live_oauth.rsplit(":", 1)[1])
    r = raw_post(live_oauth, None, LIST, extra_headers={"Host": f"127.0.0.1:{port + 1}"})
    assert "resource_metadata" not in r.headers.get("www-authenticate", "")
