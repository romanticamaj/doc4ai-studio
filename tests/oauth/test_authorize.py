"""/oauth/authorize (spec 2026-11-05 §5.3 (1)–(3), §6): the 授權連線 page, its CSRF-safe POST, token-mode credential, iss."""
import re
import time

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
    assert f"本機註冊的用戶端（{dcr_client['client_id']}）" in html and ISSUER in html
    assert 'name="request" value="' in html and 'type="password"' not in html and TEXT["loopback_only"] in html
    assert re.search(r'value="doc4ai:read"[^>]*checked', html) and re.search(r'value="doc4ai:convert"[^>]*checked', html)
    assert not re.search(r'value="doc4ai:manage"[^>]*checked', html) and html.count(TEXT["attention"]) == 1
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "form-action" not in r.headers["content-security-policy"] and r.headers["referrer-policy"] == "same-origin"
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
             ({"client_id": "https://nobody.test/m"}, "無法取得用戶端的描述文件"),
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
    assert r.headers["referrer-policy"] == "same-origin" and r.headers["cache-control"] == "no-store"
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


class FakeClock:
    """time.time for the endpoint module, advanced by the consent throttle's sleep instead of waiting for real."""

    def __init__(self, monkeypatch, client):
        import aidoc.oauth.endpoints as E
        self.now = time.time()
        self.slept: list[float] = []
        monkeypatch.setattr(E.time, "time", lambda: self.now)

        async def sleep(d):
            self.slept.append(d)
            self.now += d
        client.app.state.oauth.sleep = sleep


def test_token_mode_requires_server_token(token_oauth_client, token_oauth_ctx, monkeypatch):
    clock = FakeClock(monkeypatch, token_oauth_client)
    row = register_client(token_oauth_client)
    html = _fresh(token_oauth_client, row)
    assert 'type="password" name="server_token"' in html and TEXT["loopback_only"] not in html
    assert consent_post(token_oauth_client, html).status_code == 401                         # no token
    r = consent_post(token_oauth_client, html, server_token="wrong")
    assert r.status_code == 401 and TEXT["err_wrong_token"] in r.text
    assert consent_post(token_oauth_client, html, server_token=SERVER_TOKEN).status_code == 429   # this page pauses 1 s
    clock.now += 1
    assert consent_post(token_oauth_client, html, server_token=SERVER_TOKEN).status_code == 302   # the same page still works
    assert consent_post(token_oauth_client, html, server_token=SERVER_TOKEN).status_code == 400   # ...once
    assert consent_post(token_oauth_client, _fresh(token_oauth_client, row), server_token=SERVER_TOKEN,
                        decision="deny").status_code == 302


def _fresh(client, row):
    return client.get("/oauth/authorize", params=auth_params(row)).text


def test_shared_address_cannot_lock_the_owner_out(token_oauth_client, token_oauth_ctx, monkeypatch):
    """Review 2026-11-06 #3: wrong guesses from an address (a LAN / NAT neighbour) slow that address down, but the
    right token on a fresh page still gets in: there is no lockout keyed by the address alone."""
    clock = FakeClock(monkeypatch, token_oauth_client)
    row = register_client(token_oauth_client)
    for _ in range(25):
        assert consent_post(token_oauth_client, _fresh(token_oauth_client, row), server_token="wrong").status_code in (401, 429)
    r = consent_post(token_oauth_client, _fresh(token_oauth_client, row), server_token=SERVER_TOKEN)
    assert r.status_code == 302 and "code=" in r.headers["location"]
    assert max(clock.slept) <= 8                                          # a short pause, never a long refusal
    clock.slept.clear()
    consent_post(token_oauth_client, _fresh(token_oauth_client, row), server_token="wrong")
    assert clock.slept == []                                              # success reset the address's pause


def test_wrong_guess_throughput_is_bounded(token_oauth_client, token_oauth_ctx, monkeypatch):
    """Every attempt from an address waits its pause, and the pause grows with the failures: an attacker never gets
    more than a handful of server-token guesses a minute, however many pages it opens."""
    clock = FakeClock(monkeypatch, token_oauth_client)
    row = register_client(token_oauth_client)
    t0, judged = clock.now, []
    while clock.now - t0 < 300:
        r = consent_post(token_oauth_client, _fresh(token_oauth_client, row), server_token="guess")
        if r.status_code == 401:
            judged.append(clock.now)
        else:
            clock.now += 1                                                # refused (rate limit): the attacker retries
    per_minute = [sum(1 for t in judged if m * 60 <= t - t0 < (m + 1) * 60) for m in range(5)]
    assert max(per_minute[1:]) <= 8, per_minute                           # after the first few: bounded by the pause
    assert sum(per_minute) <= 50, per_minute


def test_throttle_and_refusals_do_not_flood_the_call_log(token_oauth_client, token_oauth_ctx, monkeypatch):
    """Review #1: refused attempts go through the auth-failure guard's aggregate row; one row marks the slow-down."""
    FakeClock(monkeypatch, token_oauth_client)
    row = register_client(token_oauth_client)
    for _ in range(80):
        consent_post(token_oauth_client, _fresh(token_oauth_client, row), server_token="wrong")
    rows = [c for c in token_oauth_ctx.store.list_mcp_calls(limit=500)]
    individual = [c for c in rows if c["method"] == "oauth/authorize" and c["status"] in ("auth_error", "rate_limited")]
    assert len(individual) <= 12, len(individual)                         # ~10 before the guard aggregates, + the transition
    assert sum(1 for c in individual if c["error_code"] == "consent_slowed") == 1
    assert any(c["status"] == "rate_limited" and c["error_code"] == "auth_failures" for c in rows)    # the aggregate row
    assert all("wrong" not in str(c) and SERVER_TOKEN not in str(c) for c in rows)


def test_consent_nonce_burned_after_wrong_guesses(token_oauth_client, token_oauth_ctx, monkeypatch):
    """The hard limit is per page: at most 5 wrong server tokens, with a growing pause between them; then it is used up
    and the user reloads for a new page."""
    clock = FakeClock(monkeypatch, token_oauth_client)
    row = register_client(token_oauth_client)
    html = _fresh(token_oauth_client, row)
    assert consent_post(token_oauth_client, html, server_token="w1").status_code == 401
    assert consent_post(token_oauth_client, html, server_token="w2").status_code == 401
    r = consent_post(token_oauth_client, html, server_token=SERVER_TOKEN)      # 1 s pause after the 2nd wrong guess
    assert r.status_code == 429 and r.headers["retry-after"] == "1"
    waited = 1
    for i, pause in ((3, 2), (4, 4)):
        clock.now += waited                                                    # wait out the page's pause
        assert consent_post(token_oauth_client, html, server_token=f"w{i}").status_code == 401
        r = consent_post(token_oauth_client, html, server_token=SERVER_TOKEN)
        assert r.status_code == 429 and r.headers["retry-after"] == str(pause)
        waited = pause
    clock.now += waited
    r = consent_post(token_oauth_client, html, server_token="w5")
    assert r.status_code == 400 and TEXT["err_used"] in r.text                # the 5th wrong guess uses the page up
    assert _codes(token_oauth_ctx) == 0
    assert consent_post(token_oauth_client, _fresh(token_oauth_client, row), server_token=SERVER_TOKEN).status_code == 302


def test_used_up_page_is_no_token_oracle(token_oauth_client, token_oauth_ctx, monkeypatch):
    """Review #2: a burned or already-used page answers identically whatever token is sent (checked before any
    comparison), so it cannot be used to test guesses."""
    clock = FakeClock(monkeypatch, token_oauth_client)
    row = register_client(token_oauth_client)
    burned = _fresh(token_oauth_client, row)
    for i in range(5):
        clock.now += 5                                                         # past each page pause (1, 2, 4 s)
        assert consent_post(token_oauth_client, burned, server_token=f"w{i}").status_code in (401, 400)
    used = _fresh(token_oauth_client, row)
    clock.now += 5
    assert consent_post(token_oauth_client, used, server_token=SERVER_TOKEN).status_code == 302
    for html in (burned, used):
        answers = []
        for tok in ("wrong", SERVER_TOKEN, ""):
            clock.now += 5
            r = consent_post(token_oauth_client, html, server_token=tok)
            answers.append((r.status_code, r.text, r.headers.get("retry-after")))
        assert len(set(answers)) == 1 and answers[0][0] == 400 and TEXT["err_used"] in answers[0][1]


def test_throttle_arithmetic_is_clamped_and_fails_closed():
    """Review #4: huge counters never overflow or skip the check; the pause stays at its cap."""
    from aidoc.oauth.consent import ConsentThrottle
    t = ConsentThrottle()
    t._ip["1.2.3.4"] = {"fails": 10 ** 9, "last": 1000.0}
    assert t.delay_for("1.2.3.4", 1001.0) == t.MAX_DELAY_S
    t._nonce["n"] = (10 ** 9, 0.0)
    t.failure("1.2.3.4", "n", 1001.0)
    assert t._ip["1.2.3.4"]["fails"] <= t.FREE_FAILS + t.EXP_CAP + 1
    assert 0 < t.nonce_retry("n", 1001.0) <= 2 ** t.EXP_CAP
    t._ip["5.6.7.8"] = {"fails": "garbage", "last": None}                   # any arithmetic error: the longest pause
    assert t.delay_for("5.6.7.8", 1001.0) == t.MAX_DELAY_S
    t._ip["1.2.3.4"]["last"] = 0.0
    assert t.delay_for("1.2.3.4", 1000.0 + t.QUIET_S) == 0                 # decays after a quiet period


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


@pytest.mark.parametrize("over", [{"response_type": "token"}, {"code_challenge_method": "plain"}, {"scope": "bogus"},
                                  {"resource": "https://elsewhere.example/mcp"}])
def test_errors_never_redirect_to_an_https_client(oauth_client, fake_cimd, over):
    """Verifier F3 (RFC 9700 §4.11.2): before any consent, an error is redirected only to a loopback or private-use
    redirect URI (a program on this computer). An https redirect URI gets the error page here, so an attacker's
    client document cannot make this server an open redirector."""
    url = "https://evil.example/cimd.json"
    fake_cimd.put(url, {"client_id": url, "client_name": "x", "redirect_uris": ["https://evil.example/land"]})
    row = {"client_id": url}
    r = oauth_client.get("/oauth/authorize", params=auth_params(row, redirect_uri="https://evil.example/land", **over),
                         follow_redirects=False)
    assert r.status_code == 400 and "location" not in r.headers and "evil.example/land?" not in r.text
    assert TEXT["error_title"] in r.text
    dcr = register_client(oauth_client, redirect_uris=("https://www.cursor.com/agents/mcp/oauth/callback",), name="DCR web")
    r = oauth_client.get("/oauth/authorize", params=auth_params(dcr, redirect_uri="https://www.cursor.com/agents/mcp/oauth/callback",
                                                                **over), follow_redirects=False)
    assert r.status_code == 400 and "location" not in r.headers


def test_errors_still_return_to_loopback_clients(oauth_client):
    """Private-use schemes get the local page before consent unless the client holds a live grant (review 2026-11-06,
    tests/oauth/test_redirect_schemes.py)."""
    for uri, presented in (("http://127.0.0.1/callback", CB), ("http://localhost/callback", "http://localhost:4444/callback")):
        row = register_client(oauth_client, redirect_uris=(uri,))
        r = oauth_client.get("/oauth/authorize", params=auth_params(row, redirect_uri=presented, response_type="token"),
                             follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"].startswith(presented + "?error=unsupported_response_type")


def test_https_client_can_still_be_approved(oauth_client):
    """The rule only covers unauthenticated error redirects: after the user clicks, an https client gets its code."""
    row = register_client(oauth_client, redirect_uris=("https://www.cursor.com/agents/mcp/oauth/callback",))
    html = oauth_client.get("/oauth/authorize", params=auth_params(row, redirect_uri="https://www.cursor.com/agents/mcp/oauth/callback")).text
    r = consent_post(oauth_client, html)
    assert r.status_code == 302 and r.headers["location"].startswith("https://www.cursor.com/agents/mcp/oauth/callback?code=")


def test_cross_site_post_renders_the_error_card(oauth_client, dcr_client):
    """Browser tester: a refused cross-site consent POST shows the localized 無法授權 card, not raw JSON."""
    html = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client)).text
    for headers in ({"Origin": "http://evil.test"}, {"Origin": ISSUER, "Sec-Fetch-Site": "cross-site"}):
        r = consent_post(oauth_client, html, headers=headers)
        assert r.status_code == 403 and r.headers["content-type"].startswith("text/html")
        assert TEXT["error_title"] in r.text and TEXT["err_cross_site"] in r.text and '"error"' not in r.text
        assert r.headers["x-frame-options"] == "DENY" and "location" not in r.headers


def _browser_form(html: str, untick_all: bool = True) -> dict:
    """What a browser submits for this page: hidden inputs, plus checked, enabled checkboxes (none when untick_all)."""
    fields: dict[str, list[str]] = {}
    for tag in re.findall(r"<input[^>]*>", html):
        name = re.search(r'name="([^"]+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if not name:
            continue
        kind = re.search(r'type="([^"]+)"', tag).group(1)
        if kind == "hidden" or (kind == "checkbox" and "checked" in tag and "disabled" not in tag and not untick_all):
            fields.setdefault(name.group(1), []).append(value.group(1) if value else "")
    return fields


def test_unticking_every_optional_scope_is_not_a_dead_end(oauth_client, dcr_client):
    """Browser tester: with every box unticked the page used to end on a 400. doc4ai:read is required: shown checked
    and disabled (必要) and always sent as a hidden field, so the submit grants read only."""
    v, c = pkce()
    html = oauth_client.get("/oauth/authorize", params=auth_params(dcr_client, c, scope="doc4ai:read doc4ai:convert doc4ai:manage")).text
    assert re.search(r'<input type="checkbox" value="doc4ai:read" checked disabled>', html)
    assert '<input type="hidden" name="scopes" value="doc4ai:read">' in html and TEXT["required"] in html
    r = oauth_client.post("/oauth/authorize", data={**_browser_form(html), "decision": "allow"},
                          headers={"Origin": ISSUER, "Sec-Fetch-Site": "same-origin"}, follow_redirects=False)
    assert r.status_code == 302, r.text
    t = exchange(oauth_client, dcr_client, location_query(r)["code"], v)
    assert t.json()["scope"] == "doc4ai:read"
