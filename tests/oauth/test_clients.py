"""Client registry (spec 2026-11-05 §5.1 CIMD, §5.2 DCR + redirect rules)."""
import time

import pytest

from aidoc.oauth.clients import ClientError, ClientRegistry, classify_redirect, redirect_allowed
from tests.oauth.conftest import CLAUDE_CODE_CIMD

GOOD = CLAUDE_CODE_CIMD
T0 = 1_800_000_000.0


def test_cimd_fetch_rules(fake_cimd, oauth_ctx):
    reg = ClientRegistry(oauth_ctx)
    fake_cimd.put(GOOD["client_id"], GOOD, headers={"Cache-Control": "public, max-age=3600"})
    c = reg.resolve(GOOD["client_id"], now=T0)
    assert c["kind"] == "cimd" and c["name"] == "Claude Code" and fake_cimd.calls == 1
    assert c["redirect_uris"] == GOOD["redirect_uris"] and c["client_uri"] == "https://claude.ai" and c["metadata"] == GOOD
    reg.resolve(GOOD["client_id"], now=T0 + 60)
    assert fake_cimd.calls == 1                                             # cached (in the store)
    assert ClientRegistry(oauth_ctx).resolve(GOOD["client_id"], now=T0 + 120)["name"] == "Claude Code"
    assert fake_cimd.calls == 1                                             # the cache survives a restart
    reg.resolve(GOOD["client_id"], now=T0 + 4000)
    assert fake_cimd.calls == 2                                             # expired -> refetched
    for bad_id in ("http://claude.ai/x", "https://claude.ai", "https://claude.ai/", "https://u:p@claude.ai/x",
                   "https://claude.ai/x#f", "https://claude.ai/x?q=1", "https://" + "a" * 600 + ".test/x"):
        with pytest.raises(ClientError):
            reg.resolve(bad_id, now=T0)
    fake_cimd.put("https://evil.test/m", {**GOOD, "client_id": "https://other.test/m"})
    with pytest.raises(ClientError, match="client_id"):                    # document must echo its own URL
        reg.resolve("https://evil.test/m", now=T0)
    fake_cimd.put("https://secret.test/m", GOOD | {"client_id": "https://secret.test/m", "token_endpoint_auth_method": "client_secret_basic"})
    with pytest.raises(ClientError, match="auth_method"):
        reg.resolve("https://secret.test/m", now=T0)
    fake_cimd.put("https://nouris.test/m", GOOD | {"client_id": "https://nouris.test/m", "redirect_uris": []})
    with pytest.raises(ClientError, match="redirect_uris"):
        reg.resolve("https://nouris.test/m", now=T0)
    fake_cimd.put("https://html.test/m", GOOD | {"client_id": "https://html.test/m"}, content_type="text/html")
    with pytest.raises(ClientError, match="json"):
        reg.resolve("https://html.test/m", now=T0)
    fake_cimd.put("https://apptype.test/m", GOOD | {"client_id": "https://apptype.test/m", "application_type": "browser"})
    with pytest.raises(ClientError, match="application_type"):
        reg.resolve("https://apptype.test/m", now=T0)
    oauth_ctx.config.oauth.allow_cimd = False
    with pytest.raises(ClientError):
        ClientRegistry(oauth_ctx).resolve("https://claude.ai/oauth/other", now=T0)


def test_cimd_blocks_private_and_redirect(fake_cimd, oauth_ctx):
    reg = ClientRegistry(oauth_ctx)
    for i, ip in enumerate(("127.0.0.1", "10.1.2.3", "192.168.0.5", "169.254.1.1", "100.64.3.3", "::1", "fd00::1", "0.0.0.0",
                            "224.0.0.1", "::ffff:127.0.0.1")):
        fake_cimd.put("https://priv.test/m", GOOD | {"client_id": "https://priv.test/m"}, addrs=["104.18.1.1", ip])
        with pytest.raises(ClientError, match="address"):
            reg.resolve("https://priv.test/m", now=T0 + 61 * i)        # one attempt a minute: under the fetch limit
    assert fake_cimd.calls_for("https://priv.test/m") == 0                  # refused before any request
    fake_cimd.put("https://redir.test/m", None, status=302, headers={"Location": "https://claude.ai/x"})
    with pytest.raises(ClientError, match="redirect"):
        reg.resolve("https://redir.test/m", now=T0)
    fake_cimd.put("https://big.test/m", {"client_id": "https://big.test/m", "pad": "x" * 70000, "redirect_uris": ["https://a/b"],
                                         "client_name": "b"})
    with pytest.raises(ClientError, match="64"):
        reg.resolve("https://big.test/m", now=T0)
    fake_cimd.put("https://err.test/m", None, status=500)
    with pytest.raises(ClientError):
        reg.resolve("https://err.test/m", now=T0)
    assert oauth_ctx.store.get_oauth_client("https://err.test/m") is None    # errors are never persisted
    for _ in range(7):
        with pytest.raises(ClientError):
            reg.resolve("https://err.test/m", now=T0 + 1)
    assert fake_cimd.calls_for("https://err.test/m") == 1                    # answered from the 30 s negative cache
    with pytest.raises(ClientError):
        reg.resolve("https://err.test/m", now=T0 + 61)
    assert fake_cimd.calls_for("https://err.test/m") == 2                    # after the window it fetches again
    # the per-client limit (6 a minute) stays as the backstop behind the negative cache
    assert [reg._may_fetch("https://other.test/m", T0) for _ in range(7)] == [True] * 6 + [False]
    assert reg._may_fetch("https://other.test/m", T0 + 60) is True


def test_cimd_stale_document_used_when_fetch_fails(fake_cimd, oauth_ctx):
    reg = ClientRegistry(oauth_ctx)
    fake_cimd.put(GOOD["client_id"], GOOD)
    reg.resolve(GOOD["client_id"], now=T0)
    fake_cimd.put(GOOD["client_id"], None, status=503)
    assert reg.resolve(GOOD["client_id"], now=T0 + 90000)["name"] == "Claude Code"   # cache expired, fetch failed, stale ok (< 7 d)
    with pytest.raises(ClientError, match="503"):
        reg.resolve(GOOD["client_id"], now=T0 + 8 * 86400)


def test_dcr_client_resolution(oauth_ctx):
    reg = ClientRegistry(oauth_ctx)
    row = reg.register_dcr({"client_name": "My IDE", "redirect_uris": ["http://localhost/cb"]}, "127.0.0.1", T0)
    assert reg.resolve(row["client_id"], now=T0)["kind"] == "dcr"
    with pytest.raises(ClientError, match="找不到"):
        reg.resolve("doc4ai_cl_nobody", now=T0)
    oauth_ctx.store.con.execute("UPDATE oauth_clients SET disabled_at=1 WHERE client_id=?", (row["client_id"],))
    with pytest.raises(ClientError):
        reg.resolve(row["client_id"], now=T0)


@pytest.mark.parametrize("registered,presented,ok", [
    ("http://localhost/callback", "http://localhost:3118/callback", True),
    ("http://127.0.0.1/callback", "http://127.0.0.1:60123/callback", True),
    ("http://[::1]/callback", "http://[::1]:60123/callback", True),
    ("http://localhost:8787/callback", "http://localhost:3118/callback", True),
    ("http://localhost/callback", "http://127.0.0.1:3118/callback", False),        # host must match
    ("http://localhost/callback", "http://localhost:3118/other", False),
    ("http://localhost/callback", "http://localhost.evil:3118/callback", False),
    ("http://localhost/callback", "https://localhost:3118/callback", False),
    ("http://localhost/callback", "http://localhost:3118/callback#x", False),
    ("http://localhost/callback", "http://localhost:3118/callback?x=1", False),
    ("http://localhost/callback", "http://user@localhost:3118/callback", False),
    ("https://www.cursor.com/agents/mcp/oauth/callback", "https://www.cursor.com/agents/mcp/oauth/callback", True),
    ("https://www.cursor.com/agents/mcp/oauth/callback", "https://www.cursor.com/agents/mcp/oauth/callback/", False),
    ("https://www.cursor.com/cb", "https://www.cursor.com:8443/cb", False),
    ("cursor://anysphere.cursor-mcp/oauth/callback", "cursor://anysphere.cursor-mcp/oauth/callback", True),
    ("cursor://anysphere.cursor-mcp/oauth/callback", "cursor://anysphere.cursor-mcp/oauth/callback?x=1", False),
    ("http://evil.example/cb", "http://evil.example/cb", False),                   # never a valid redirect
])
def test_redirect_matching_rules(registered, presented, ok):
    assert redirect_allowed({"redirect_uris": [registered]}, presented) is ok


def test_classify_redirect():
    assert classify_redirect("https://a.example/cb") == "https"
    assert classify_redirect("http://127.0.0.1:5/cb") == "loopback" and classify_redirect("http://[::1]/cb") == "loopback"
    assert classify_redirect("cursor://anysphere.cursor-mcp/oauth/callback") == "private"
    assert classify_redirect("com.example.app:/cb") == "private" and classify_redirect("cursor://x/y") is None
    for bad in ("http://evil/cb", "javascript:alert(1)", "JavaScript:x", "data:text/html,x", "file:///etc/passwd", "ftp://x/y",
                "https://a/b#f", "https:///nohost", "", "no-scheme", "1abc://x", "x://" + "a" * 600, None, 3):
        assert classify_redirect(bad) is None, bad


@pytest.mark.parametrize("uri,accepted", [("http://evil.example/cb", False), ("http://localhost:8787/callback", True),
    ("http://[::1]/cb", True), ("javascript:alert(1)", False), ("data:text/html,x", False), ("file:///etc/passwd", False),
    ("vbscript:x", False), ("cursor://anysphere.cursor-mcp/oauth/callback", True), ("com.example.app:/cb", True),
    ("https://a/b#frag", False), ("ftp://x/y", False)])
def test_dcr_redirect_rules(oauth_client, uri, accepted):
    r = oauth_client.post("/oauth/register", json={"redirect_uris": [uri], "client_name": "x", "token_endpoint_auth_method": "none"})
    assert (r.status_code == 201) is accepted and (accepted or r.json()["error"] == "invalid_redirect_uri"), r.text


def test_cursor_registration_payload_is_accepted(oauth_client, oauth_ctx):
    body = {"client_name": "Cursor", "redirect_uris": ["cursor://anysphere.cursor-mcp/oauth/callback",
            "https://www.cursor.com/agents/mcp/oauth/callback", "http://localhost:8787/callback"],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "token_endpoint_auth_method": "none"}
    r = oauth_client.post("/oauth/register", json=body)
    assert r.status_code == 201 and r.json()["redirect_uris"] == body["redirect_uris"] and "client_secret" not in r.json()
    assert r.headers["cache-control"] == "no-store" and "workspace" not in r.json()
    row = oauth_ctx.store.get_oauth_client(r.json()["client_id"])
    assert row["kind"] == "dcr" and row["name"] == "Cursor" and row["metadata"] == body


def test_dcr_register_and_limits(oauth_client, oauth_ctx):
    body = {"redirect_uris": ["http://127.0.0.1/callback"], "client_name": "SDK", "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "software_id": "sdk-x",
            "software_version": "2.2", "application_type": "native", "client_uri": "https://example.com", "scope": "doc4ai:read"}
    r = oauth_client.post("/oauth/register", json=body)
    j = r.json()
    assert r.status_code == 201 and j["client_id"].startswith("doc4ai_cl_") and j["token_endpoint_auth_method"] == "none"
    assert isinstance(j["client_id_issued_at"], int) and j["application_type"] == "native" and j["software_id"] == "sdk-x"
    row = oauth_ctx.store.get_oauth_client(j["client_id"])
    assert row["kind"] == "dcr" and row["software_id"] == "sdk-x" and row["application_type"] == "native"
    for bad, err in (({"redirect_uris": ["http://evil.example/cb"]}, "invalid_redirect_uri"),
                     ({"redirect_uris": []}, "invalid_redirect_uri"),
                     ({"redirect_uris": ["http://localhost/cb"] * 11}, "invalid_redirect_uri"),
                     ({"token_endpoint_auth_method": "client_secret_post"}, "invalid_client_metadata"),
                     ({"grant_types": ["client_credentials"]}, "invalid_client_metadata"),
                     ({"response_types": ["token"]}, "invalid_client_metadata"),
                     ({"application_type": "browser"}, "invalid_client_metadata"),
                     ({"client_name": "x" * 101}, "invalid_client_metadata")):
        b = oauth_client.post("/oauth/register", json={**body, **bad})
        assert b.status_code == 400 and b.json()["error"] == err, (bad, b.text)
    assert oauth_client.post("/oauth/register", content=b"not json", headers={"Content-Type": "application/json"}).status_code == 400
    # 1 + 8 bad + 1 non-JSON = 10 attempts this hour; the flood ceiling is 20 × DCR_ATTEMPT_FACTOR per source.
    # Past 20 unused registrations the oldest unused one gives way (verifier F2), so registering keeps working ...
    from aidoc.oauth.clients import DCR_ATTEMPT_FACTOR
    ceiling = oauth_ctx.config.oauth.dcr_per_hour_per_ip * DCR_ATTEMPT_FACTOR
    for _ in range(ceiling - 10):
        assert oauth_client.post("/oauth/register", json=body).status_code == 201
    assert len([c for c in oauth_ctx.store.list_oauth_clients() if c["kind"] == "dcr"]) == 20
    # ... until the flood ceiling
    r = oauth_client.post("/oauth/register", json=body)
    assert r.status_code == 429 and r.json()["error"] == "rate_limited" and "retry-after" in r.headers
    oauth_ctx.config.oauth.allow_dcr = False
    assert oauth_client.post("/oauth/register", json=body).status_code == 404


def test_dcr_window_resets(oauth_ctx):
    reg = ClientRegistry(oauth_ctx)
    oauth_ctx.config.oauth.dcr_per_hour_per_ip = 2
    body = {"redirect_uris": ["http://localhost/cb"]}
    from aidoc.oauth.clients import DCR_ATTEMPT_FACTOR
    for i in range(2 * DCR_ATTEMPT_FACTOR):                                # the flood ceiling: 2 × DCR_ATTEMPT_FACTOR
        reg.register_dcr(body, "1.1.1.1", T0 + i / 100)
    with pytest.raises(ClientError, match="rate"):
        reg.register_dcr(body, "1.1.1.1", T0 + 2)
    reg.register_dcr(body, "2.2.2.2", T0 + 2)                              # another address has its own window
    reg.register_dcr(body, "1.1.1.1", T0 + 3601)                           # a new hour
    assert time.time() > 0
