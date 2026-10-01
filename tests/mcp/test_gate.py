import json
import time

import pytest
from starlette.testclient import TestClient

from aidoc.mcp import tokens as T
from aidoc.mcp.calllog import CallRecorder
from aidoc.mcp.gate import CallState, McpGate, max_body_bytes, www_authenticate
from aidoc.mcp.principal import PatVerifier
from aidoc.mcp.ratelimit import RateLimiter

SECRET = b"s" * 32
MODERN = {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/call", "Content-Type": "application/json",
          "Accept": "application/json, text/event-stream"}


async def echo_inner(scope, receive, send):
    """Stands in for the SDK app: echoes what the gate put in scope.state and what body it received."""
    body = b""
    while True:
        m = await receive()
        body += m.get("body", b"")
        if not m.get("more_body"):
            break
    st = scope["state"]["doc4ai"]
    status = 421 if scope["headers"] and dict(scope["headers"]).get(b"host", b"").startswith(b"evil") else 200
    out = json.dumps({"token_id": st.principal.token_id, "method": st.method, "ip": st.ip, "ua": st.user_agent,
                      "body": body.decode(), "pv": st.protocol_version}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": out})


@pytest.fixture
def gate_client(ctx):
    rec = CallRecorder(ctx)
    gate = McpGate(ctx, echo_inner, PatVerifier(ctx.store, SECRET), RateLimiter(lambda: ctx.config.mcp.rate_limit_per_min),
                   rec)
    c = TestClient(gate, base_url="http://127.0.0.1:8765", client=("100.64.0.9", 5555))
    return c, ctx


def _issue(ctx, scopes=("doc4ai:read",), rate=None):
    raw = T.generate_token()
    tid = ctx.store.create_api_token(name="t", prefix=T.display_prefix(raw), token_hash=T.token_hash(SECRET, raw),
                                     scopes=list(scopes), expires_at=time.time() + 3600, rate_limit_per_min=rate)
    return raw, tid


def _rows(ctx):
    return ctx.store.list_mcp_calls(limit=50)


def test_max_body_and_header_helpers(ctx):
    assert max_body_bytes(ctx.config) == 20 * 1024 * 1024 * 4 // 3 + 1024 * 1024
    assert www_authenticate("invalid_token", None) == 'Bearer realm="doc4ai", error="invalid_token"'
    assert www_authenticate("invalid_token", "x", "https://h/.well-known/oauth-protected-resource").endswith(
        'error_description="x", resource_metadata="https://h/.well-known/oauth-protected-resource"')


def test_disabled_is_404_and_not_logged(gate_client):
    c, ctx = gate_client
    ctx.config.mcp.enabled = False
    r = c.post("/mcp", content=b"{}", headers=MODERN)
    assert r.status_code == 404 and r.json() == {"error": "mcp_disabled"}
    assert _rows(ctx) == []
    ctx.config.mcp.enabled = True


def test_missing_token_401_logged(gate_client):
    c, ctx = gate_client
    r = c.post("/mcp", content=b'{"method":"tools/list"}', headers=MODERN)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith('Bearer realm="doc4ai", error="invalid_token"')
    assert "resource_metadata" not in r.headers["www-authenticate"]
    assert r.json()["error"] == "invalid_token"
    row = _rows(ctx)[0]
    assert row["status"] == "auth_error" and row["error_code"] == "missing_token" and row["http_status"] == 401
    assert row["ip"] == "100.64.0.9" and row["token_prefix_seen"] is None


def test_query_token_rejected_even_when_valid(gate_client):
    c, ctx = gate_client
    raw, _ = _issue(ctx)
    r = c.post(f"/mcp?token={raw}", content=b"{}", headers=MODERN)
    assert r.status_code == 400 and r.json()["error"] == "token_in_query"
    row = _rows(ctx)[0]
    assert row["status"] == "auth_error" and row["error_code"] == "token_in_query"
    assert row["token_prefix_seen"] == raw[:15] and raw not in json.dumps(row)


@pytest.mark.parametrize("variant", [
    lambda t: f"Bearer {t}", lambda t: f"bearer {t}", lambda t: f"BEARER   {t}  ", lambda t: f'Bearer "{t}"',
])
def test_token_copy_paste_variants_accepted(gate_client, variant):
    c, ctx = gate_client
    raw, tid = _issue(ctx)
    r = c.post("/mcp", content=b'{"method":"tools/list"}', headers={**MODERN, "Authorization": variant(raw)})
    assert r.status_code == 200 and r.json()["token_id"] == tid


@pytest.mark.parametrize("mutate,code", [
    (lambda t: t[:-1], "bad_format"), (lambda t: t[:-1] + ("A" if t[-1] != "A" else "B"), "bad_checksum"),
    (lambda t: T.generate_token(), "unknown_token"), (lambda t: "Basic " + t, "missing_token"),
])
def test_token_copy_paste_variants_rejected(gate_client, mutate, code):
    c, ctx = gate_client
    raw, _ = _issue(ctx)
    bad = mutate(raw)
    auth = bad if bad.startswith("Basic") else f"Bearer {bad}"
    r = c.post("/mcp", content=b"{}", headers={**MODERN, "Authorization": auth})
    assert r.status_code == 401
    row = _rows(ctx)[0]
    assert row["error_code"] == code and (row["token_prefix_seen"] or "").startswith("doc4ai_pat_") == bad.startswith("doc4ai_pat_")


def test_revoked_and_expired(gate_client):
    c, ctx = gate_client
    raw, tid = _issue(ctx)
    ctx.store.revoke_api_token(tid, "lost", at=time.time())
    r = c.post("/mcp", content=b"{}", headers={**MODERN, "Authorization": f"Bearer {raw}"})
    assert r.status_code == 401 and _rows(ctx)[0]["error_code"] == "revoked"
    raw2, tid2 = _issue(ctx)
    ctx.store.update_api_token(tid2, expires_at=time.time() - 1)
    r = c.post("/mcp", content=b"{}", headers={**MODERN, "Authorization": f"Bearer {raw2}"})
    assert r.status_code == 401 and _rows(ctx)[0]["error_code"] == "expired"


def test_state_body_replay_and_fallback_logging(gate_client):
    c, ctx = gate_client
    raw, tid = _issue(ctx)
    body = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"x"}}'
    r = c.post("/mcp", content=body, headers={**MODERN, "Authorization": f"Bearer {raw}", "User-Agent": "spike/1"})
    assert r.status_code == 200
    d = r.json()
    assert d == {"token_id": tid, "method": "tools/call", "ip": "100.64.0.9", "ua": "spike/1", "body": body.decode(),
                 "pv": "2026-07-28"}
    row = _rows(ctx)[0]                                    # echo_inner never marks state.logged: the gate logs it
    assert row["status"] == "ok" and row["token_id"] == tid and row["method"] == "tools/call" and row["http_status"] == 200
    assert ctx.store.get_api_token(tid)["last_used_ip"] == "100.64.0.9"


def test_legacy_method_from_body_and_rate_limit(gate_client):
    c, ctx = gate_client
    raw, tid = _issue(ctx, rate=2)
    legacy = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
              "Authorization": f"Bearer {raw}"}
    call = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"x","arguments":{}}}'
    lst = b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
    assert c.post("/mcp", content=call, headers=legacy).status_code == 200
    assert c.post("/mcp", content=call, headers=legacy).status_code == 200
    r = c.post("/mcp", content=call, headers=legacy)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and r.json()["error"] == "rate_limited"
    row = _rows(ctx)[0]
    assert row["status"] == "rate_limited" and row["token_id"] == tid and row["method"] == "tools/call"
    assert c.post("/mcp", content=lst, headers=legacy).status_code == 200     # tools/list is never limited
    assert c.post("/mcp", content=lst, headers=legacy).json()["method"] == "tools/list"


def test_body_too_large_413(gate_client):
    c, ctx = gate_client
    raw, _ = _issue(ctx)
    ctx.config.mcp.max_upload_mb = 1
    big = b'{"method":"tools/call","params":{"arguments":{"content_base64":"' + b"A" * (3 * 1024 * 1024) + b'"}}}'
    r = c.post("/mcp", content=big, headers={**MODERN, "Authorization": f"Bearer {raw}"})
    assert r.status_code == 413 and r.json()["error"] == "payload_too_large"
    assert _rows(ctx)[0]["status"] == "protocol_error" and _rows(ctx)[0]["error_code"] == "payload_too_large"
    ctx.config.mcp.max_upload_mb = 20


def test_inner_rejection_is_logged_as_protocol_error(gate_client):
    c, ctx = gate_client
    raw, _ = _issue(ctx)
    r = c.post("/mcp", content=b"{}", headers={**MODERN, "Authorization": f"Bearer {raw}", "Host": "evil.example:8765"})
    assert r.status_code == 421
    row = _rows(ctx)[0]
    assert row["status"] == "protocol_error" and row["error_code"] == "http_421" and row["http_status"] == 421


def test_call_state_dataclass_defaults():
    st = CallState(principal=None, ip=None, user_agent=None, method=None, ts=1.0, started=2.0, protocol_version=None)
    assert st.logged is False


def test_get_stream_is_405_after_auth(gate_client):
    """Spike S12: stateless mode never sends on the standalone GET stream, so the gate refuses it instead of letting
    the SDK hold an idle connection open."""
    c, ctx = gate_client
    assert c.get("/mcp").status_code == 401
    raw, tid = _issue(ctx)
    r = c.get("/mcp", headers={"Authorization": f"Bearer {raw}", "Accept": "text/event-stream"})
    assert r.status_code == 405 and r.headers["allow"] == "POST" and r.json()["error"] == "method_not_allowed"
    row = _rows(ctx)[0]
    assert row["status"] == "protocol_error" and row["error_code"] == "http_405" and row["token_id"] == tid
