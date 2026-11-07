"""Consent page template and signed authorization request (spec 2026-11-05 §6.1, §6.2)."""
import re

import pytest

from doc4ai.oauth import consent as C

SECRET = b"k" * 32
PARAMS = {"client_id": "https://claude.ai/oauth/claude-code-client-metadata", "redirect_uri": "http://localhost:3118/callback",
          "scope": "doc4ai:read doc4ai:convert", "state": "st", "code_challenge": "c" * 43, "resource": "http://127.0.0.1:8765/mcp"}


def view(**over):
    v = {"client_name": "Claude Code", "source_kind": "cimd", "source_label": "claude.ai",
         "redirect_uri": "http://localhost:3118/callback", "redirect_kind": "loopback", "issuer": "http://127.0.0.1:8765",
         "requested_scopes": ["doc4ai:read", "doc4ai:convert", "doc4ai:manage"],
         "checked_scopes": ["doc4ai:read", "doc4ai:convert"], "needs_token": False, "request_blob": "BLOB.sig"}
    v.update(over)
    return C.ConsentView(**v)


def test_sign_verify_round_trip_and_tamper():
    blob = C.sign_request(SECRET, PARAMS, exp=1000.0)
    got = C.verify_request(SECRET, blob, now=999.0)
    assert {k: got[k] for k in PARAMS} == PARAMS and got["exp"] == 1000.0 and re.match(r"^[0-9a-f]{32}$", got["nonce"])
    assert C.verify_request(SECRET, C.sign_request(SECRET, PARAMS, exp=1000.0), now=0)["nonce"] != got["nonce"]
    body, sig = blob.split(".")
    for bad in (body + "x." + sig, body + "." + sig[:-1] + ("0" if sig[-1] != "0" else "1"), "garbage", "", "a.b.c", body + "."):
        with pytest.raises(C.ConsentError):
            C.verify_request(SECRET, bad, now=999.0)
    with pytest.raises(C.ConsentError, match="逾時"):
        C.verify_request(SECRET, blob, now=1000.0)
    with pytest.raises(C.ConsentError):
        C.verify_request(b"o" * 32, blob, now=999.0)
    with pytest.raises(C.ConsentError):
        C.verify_request(SECRET, None, now=0)


def test_nonce_set_single_use_and_eviction():
    ns = C.NonceSet(ttl_s=600)
    assert ns.consume("a", now=0) is True and ns.consume("a", now=1) is False
    assert ns.consume("b", now=0) is True
    assert ns.consume("a", now=601) is True             # evicted after the TTL (the request blob expired long before)
    assert len(ns) <= 2


def test_render_consent_cimd_loopback():
    html = C.render_consent(view(client_name="Claude <Code>"))
    assert C.TEXT["title"] in html and "<strong>claude.ai</strong>" in html and "未驗證" in html
    assert "Claude &lt;Code&gt;" in html and "Claude <Code>" not in html
    assert "http://localhost:3118/callback" in html and C.TEXT["loopback_note"] in html and C.TEXT["private_note"] not in html
    assert "http://127.0.0.1:8765" in html and 'name="request" value="BLOB.sig"' in html
    assert re.search(r'value="doc4ai:read"[^>]*checked', html) and re.search(r'value="doc4ai:convert"[^>]*checked', html)
    assert not re.search(r'value="doc4ai:manage"[^>]*checked', html) and 'value="doc4ai:manage"' in html
    assert html.count(C.TEXT["attention"]) == 1 and 'value="doc4ai:convert:local"' not in html          # only requested scopes
    assert 'type="password"' not in html and C.TEXT["loopback_only"] in html
    assert "<script" not in html.lower() and "src=" not in html and "<link" not in html and "url(" not in html
    assert set(re.findall(r"https?://[^\s\"<]+", html)) == {"http://localhost:3118/callback", "http://127.0.0.1:8765"}
    assert 'name="decision" value="deny"' in html and 'name="decision" value="allow"' in html
    assert C.TEXT["deny"] in html and C.TEXT["allow"] in html and "prefers-color-scheme: dark" in html
    assert 'action="/oauth/authorize"' in html and 'method="post"' in html


def test_render_consent_dcr_private_token_mode():
    html = C.render_consent(view(client_name="Cursor", source_kind="dcr", source_label="doc4ai_cl_abcdEFGH1234567890xyzw",
                                 redirect_uri="cursor://anysphere.cursor-mcp/oauth/callback", redirect_kind="private",
                                 requested_scopes=["doc4ai:read", "doc4ai:convert:local"], checked_scopes=["doc4ai:read"],
                                 needs_token=True))
    assert "本機註冊的用戶端（doc4ai_cl_abcdEFGH1234567890xyzw）" in html and "<strong>claude.ai</strong>" not in html
    assert C.TEXT["private_note"] in html and "cursor://anysphere.cursor-mcp/oauth/callback" in html
    assert 'type="password" name="server_token"' in html and C.TEXT["loopback_only"] not in html
    assert html.count(C.TEXT["attention"]) == 1 and not re.search(r'value="doc4ai:convert:local"[^>]*checked', html)


def test_consent_says_in_plain_words_what_each_permission_allows():
    """The user could not read a list of scope ids: every row leads with what the app may do, the id is a small footnote."""
    scopes = ["doc4ai:read", "doc4ai:convert", "doc4ai:convert:local", "doc4ai:manage"]
    html = C.render_consent(view(requested_scopes=scopes))
    assert set(C.SCOPE_TEXT) == set(C.SCOPE_DETAIL) == set(scopes)
    for s in scopes:
        title, detail = C.SCOPE_TEXT[s], C.SCOPE_DETAIL[s]
        assert title in html and detail in html and f'<code class="scope-id">{s}</code>' in html
        assert html.index(title) < html.index(detail) < html.index(f'<code class="scope-id">{s}</code>')
        assert "doc4ai:" not in title + detail
    assert "Claude Code 想連到你的 Doc4AI 文件庫" in html and C.TEXT["intro"] in html and C.TEXT["limits"] in html
    assert "doc4ai:" not in C.TEXT["scopes_note"] + C.TEXT["err_scopes"]


def test_technical_details_stay_visible_but_after_the_permissions():
    """Small print, never hidden: the source and the callback are what tells a user the request is not what it claims."""
    html = C.render_consent(view())
    assert "<details" not in html and C.TEXT["details"] in html
    assert html.index(C.SCOPE_TEXT["doc4ai:read"]) < html.index(C.TEXT["details"]) < html.index("<strong>claude.ai</strong>")
    assert html.index("http://localhost:3118/callback") < html.index('name="decision" value="deny"')


def test_sensitive_permissions_get_a_quiet_mark_not_a_scary_word():
    """「高風險」 read as an alarm. The two sensitive rows carry a ! with a spoken name; the words are the row's own detail."""
    html = C.render_consent(view(requested_scopes=["doc4ai:read", "doc4ai:convert", "doc4ai:convert:local", "doc4ai:manage"]))
    assert "高風險" not in html and "風險" not in "".join(C.TEXT.values())
    mark = f'<span class="attn" role="img" title="{C.TEXT["attention"]}">'
    assert html.count(mark) == len(C.HIGH_RISK) == 2
    for s in C.HIGH_RISK:                                # the mark sits on its own row, next to that row's title
        assert html.index(C.SCOPE_TEXT[s]) < html.index(mark, html.index(C.SCOPE_TEXT[s])) < html.index(C.SCOPE_DETAIL[s])
    for s in ("doc4ai:read", "doc4ai:convert"):
        assert mark not in html[html.index(C.SCOPE_TEXT[s]):html.index(C.SCOPE_DETAIL[s])]


def test_page_uses_the_product_look_and_stays_self_contained():
    """Same tokens and mark as the web app (index.css, BrandMark), drawn inline: the CSP allows no image, font or script."""
    for html in (C.render_consent(view(needs_token=True)), C.render_error("x")):
        assert "oklch(0.51 0.19 270)" in html and "oklch(0.72 0.14 272)" in html       # --primary, light and dark
        assert 'class="brand"' in html and "<svg" in html and "xmlns" not in html
        assert "<script" not in html.lower() and "src=" not in html and "<link" not in html and "url(" not in html
        assert "@import" not in html and "@font-face" not in html
    assert "default-src 'none'" in C.CONSENT_HEADERS["Content-Security-Policy"]


@pytest.mark.parametrize("name,tile", [("Claude Code", "C"), ("cursor", "C"), ("<b>", "&lt;"), ("  ", "?"), ("", "?"), ("克勞德", "克")])
def test_client_tile_shows_the_escaped_first_letter(name, tile):
    assert f'<span class="tile peer" aria-hidden="true">{tile}</span>' in C.render_consent(view(client_name=name))


def test_limits_text_matches_the_tools_that_exist():
    """The page promises the app cannot delete documents: adding such a tool must fail here until the text changes."""
    from doc4ai.mcp.middleware import TOOL_SCOPES
    assert "刪除文件" in C.TEXT["limits"]
    assert not [t for t in TOOL_SCOPES if "delete" in t or "remove" in t or "setting" in t or "token" in t]


def test_render_error_and_headers():
    html = C.render_error(C.TEXT["err_bad_redirect"], "<x>")
    assert C.TEXT["error_title"] in html and C.TEXT["err_bad_redirect"] in html and "&lt;x&gt;" in html and "<script" not in html
    h = C.CONSENT_HEADERS
    assert h["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert "default-src 'none'" in h["Content-Security-Policy"] and "form-action" not in h["Content-Security-Policy"]
    assert h["Referrer-Policy"] == "same-origin" and h["Cache-Control"] == "no-store" and h["X-Content-Type-Options"] == "nosniff"


def test_text_constants_exist():
    for key in ("title", "headline", "unverified", "source", "redirect", "server", "loopback_note", "private_note", "dcr_source",
                "scopes", "scopes_note", "attention", "server_token", "loopback_only", "deny", "allow", "error_title",
                "err_bad_redirect", "err_unknown_client", "err_cimd", "err_missing_state", "err_bad_host"):
        assert C.TEXT[key]
    assert C.TEXT["title"] == "授權連線" and C.TEXT["loopback_only"] == "只有在這台電腦的瀏覽器能看到這個頁面。"
    assert C.TEXT["err_bad_redirect"] == "回呼位址不在這個用戶端登記的清單內"


def test_user_visible_text_says_yonghuduan_not_client():
    """Browser tester: one language for what a person reads — 用戶端, never the English "client"."""
    import re
    for v in (view(), view(source_kind="dcr", source_label="doc4ai_cl_x", redirect_uri="cursor://a.b/cb", redirect_kind="private",
                           needs_token=True)):
        text = re.sub(r"<[^>]+>", " ", C.render_consent(v))
        assert not re.search(r"\bclient\b", text, re.IGNORECASE), text
        assert "用戶端" in text
    for key, value in C.TEXT.items():
        assert not re.search(r"\bclient\b", value, re.IGNORECASE), key
