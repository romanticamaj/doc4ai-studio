"""Consent page template and signed authorization request (spec 2026-11-05 §6.1, §6.2)."""
import re

import pytest

from aidoc.oauth import consent as C

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
    assert html.count(C.TEXT["high_risk"]) == 1 and 'value="doc4ai:convert:local"' not in html          # only requested scopes
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
    assert "本機註冊的 client（doc4ai_cl_abcdEFGH1234567890xyzw）" in html and "<strong>claude.ai</strong>" not in html
    assert C.TEXT["private_note"] in html and "cursor://anysphere.cursor-mcp/oauth/callback" in html
    assert 'type="password" name="server_token"' in html and C.TEXT["loopback_only"] not in html
    assert html.count(C.TEXT["high_risk"]) == 1 and not re.search(r'value="doc4ai:convert:local"[^>]*checked', html)


def test_render_error_and_headers():
    html = C.render_error(C.TEXT["err_bad_redirect"], "<x>")
    assert C.TEXT["error_title"] in html and C.TEXT["err_bad_redirect"] in html and "&lt;x&gt;" in html and "<script" not in html
    h = C.CONSENT_HEADERS
    assert h["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert "default-src 'none'" in h["Content-Security-Policy"] and "form-action" not in h["Content-Security-Policy"]
    assert h["Referrer-Policy"] == "no-referrer" and h["Cache-Control"] == "no-store" and h["X-Content-Type-Options"] == "nosniff"


def test_text_constants_exist():
    for key in ("title", "headline", "unverified", "source", "redirect", "server", "loopback_note", "private_note", "dcr_source",
                "scopes", "scopes_note", "high_risk", "server_token", "loopback_only", "deny", "allow", "error_title",
                "err_bad_redirect", "err_unknown_client", "err_cimd", "err_missing_state", "err_bad_host"):
        assert C.TEXT[key]
    assert C.TEXT["title"] == "授權連線" and C.TEXT["loopback_only"] == "只有在這台電腦的瀏覽器能看到這個頁面。"
    assert C.TEXT["err_bad_redirect"] == "回呼位址不在這個 client 登記的清單內"
