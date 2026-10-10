# MCP Distribution & Hardening — Part 4b: Public HTTPS and OAuth 2.1

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax. Read `docs/superpowers/plans/2026-10-05-mcp-distribution-index.md` first (§0, §3 D6–D9, §4 schema v6 + Store surface, §5 M1/M6–M8, §7 gates **G1–G3**) and spec §4–§8 of `docs/superpowers/specs/2026-10-05-mcp-distribution-hardening-design.md`. **Do not start Task B2's Funnel step or set `oauth.enabled = true` on the live server before gates G1–G3 are recorded in the deviations section.**

**Goal:** Reach the MCP server from claude.ai / Claude Desktop connectors, Claude Code (OAuth) and ChatGPT: a public HTTPS origin (Tailscale Funnel by default) with proxy-header trust and a canonical resource URL; an embedded OAuth 2.1 authorization server (PRM RFC 9728, AS metadata RFC 8414 + OIDC alias, CIMD, DCR, PKCE S256 only, RFC 8707 `resource`, RFC 9207 `iss`, refresh rotation with replay detection, revocation) issuing opaque HMAC-hashed tokens; a verifier chain that keeps PATs untouched and adds an external-IdP JWT branch; a consent screen authenticated by the server token; admin API + UI; a security suite with one test per spec §8 row; real-client acceptance.

**Conventions for every task:** fast suite `uv run pytest -m "not slow" -q` (now including `tests/oauth/` and `tests/mcp` parametrised by `oauth.enabled`) and `ruff check src tests` green before each commit; fixtures from `tests/conftest.py`, `tests/mcp/conftest.py`, `tests/oauth/conftest.py` (Task B3); commit trailer from the index §0.

---

### Task B1: SPIKE — Funnel behaviour, Claude CIMD document, python-sdk hooks, PyJWT

**Files:** Create `docs/superpowers/spikes/2026-10-oauth.md`.

**Questions:**

- **S-B1 Claude's hosted CIMD URL.** Anthropic documents Claude Code's document (`https://claude.ai/oauth/claude-code-client-metadata`) but not the claude.ai/Desktop one. Probe candidates (`https://claude.ai/.well-known/oauth-client`, `https://claude.ai/oauth/client-metadata`, `https://claude.com/.well-known/...`) and search the connector docs again on the day; if still unpublished, the acceptance test (B14) records the `client_id` the live connector actually sends on `/oauth/authorize` and the spike stores it. **Decision rule:** none needed for the code (any https `client_id` works); only the consent-screen screenshot test uses it.
- **S-B2 Funnel headers and timeouts.** With `tailscale funnel --bg 8765` pointing at a scratch uvicorn echo app: record the headers seen (`X-Forwarded-For`, `X-Forwarded-Proto`, `X-Forwarded-Host`, `Tailscale-Funnel-Request`), the source IP, whether an SSE stream survives 10 minutes idle (send `: ping` every 15 s — our SSE already does), and the slowest acceptable long-poll (`compat` 600 s sync). **Decision rule:** idle cut-off < 600 s → `docs/deploy/https.md` tells compat users to use the async endpoints and `compat.sync_timeout_s` default stays (Funnel is a deployment choice); otherwise nothing changes.
- **S-B3 Egress and 3xx.** From the public URL: `curl -I https://<host>/mcp` returns 401 with no redirect; `https://<host>` (root) serves the SPA — fine, but confirm no `http→https` redirect sits in front (Funnel only serves HTTPS). Record Anthropic's documented egress `160.79.104.0/21` for the optional allowlist note.
- **S-B4 python-sdk auth hooks.** We keep our own ASGI gate (MCP plan D1). Confirm `mcp` 2.2's `TransportSecurityMiddleware` does not reject requests whose `Host` is the public hostname when it is in `allowed_hosts` and that nothing in the SDK path reads `Authorization` (so OAuth tokens never reach SDK code). Check whether the SDK exports an `AccessToken`/`get_access_token()` we might want for `_meta` — we do not use it; record.
- **S-B5 PyJWT availability.** `uv run python -c "import jwt, cryptography; print(jwt.__version__)"` — `pyjwt[crypto]` is a dependency of `mcp`; pin nothing new. Confirm `jwt.PyJWKClient` and `algorithms=["RS256", "ES256"]` work offline against a test JWKS served by `live_server`.
- **S-B6 `uvicorn` proxy flags from `_run_uvicorn`.** `uvicorn.Config(proxy_headers=True, forwarded_allow_ips="127.0.0.1,::1", root_path="/prefix")`: confirm `request.url.scheme == "https"` and `request.client.host` behind a fake `X-Forwarded-For` from 127.0.0.1, and that a forwarded header from a non-trusted address is ignored.

- [ ] **Step 1:** Run the probes (Funnel on a scratch port, torn down afterwards with `tailscale funnel reset`); write the spike file.
- [ ] **Step 2: Commit** `docs(spike): OAuth/HTTPS — Funnel headers, Claude CIMD, SDK hooks, PyJWT`.

---

### Task B2: `server.public_url`, proxy trust, canonical resource URL, HTTPS docs

**Files:** Modify `src/aidoc/config.py` (`Server.public_url: str = ""`, `Server.trusted_proxies: list[str] = ["127.0.0.1", "::1"]`; `_check_server()` → `ConfigError` unless empty or `https://` with a host), `src/aidoc/cli.py` (`serve --public-url`; `_run_uvicorn(app, host, port, *, proxy_headers, forwarded_allow_ips, root_path)`), `src/aidoc/mcp/server.py` (`canonical_resource(cfg) -> str | None` = `public_url.rstrip("/") + "/mcp"`; `_hosts_for()` adds the public host; `endpoint_urls()` lists `https://<public>/mcp` first), `src/aidoc/server/api/settings.py` (`server.public_url`/`trusted_proxies` read-only → existing 403 `server_readonly`), `src/aidoc/server/api/mcp.py` (`status.public_url`, `status.canonical_resource`), `web/src/features/mcp/OverviewTab.tsx` (「公開端點」 card + notice), create `docs/deploy/https.md` (spec §4.2 table + Funnel steps + Cloudflare/Caddy alternatives + nginx buffering note + "no redirects" rule); tests `tests/unit/test_public_url.py`, `tests/api/test_serve.py` (append), Vitest for the card.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_public_url.py
import pytest


def test_public_url_validation(tmp_path):
    from aidoc.config import ConfigError, load_config
    p = tmp_path / "aidoc.toml"
    p.write_text('[server]\npublic_url = "https://box.tail74077f.ts.net"\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.server.public_url == "https://box.tail74077f.ts.net" and cfg.server.trusted_proxies == ["127.0.0.1", "::1"]
    for bad in ('public_url = "http://box"', 'public_url = "box.example"', 'public_url = "https://"'):
        p.write_text(f"[server]\n{bad}\n", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(p)


def test_canonical_resource_and_hosts():
    from aidoc.config import AidocConfig
    from aidoc.mcp.server import canonical_resource, endpoint_urls, transport_security_for
    cfg = AidocConfig()
    assert canonical_resource(cfg) is None
    cfg.server.public_url = "https://box.tail74077f.ts.net/"
    assert canonical_resource(cfg) == "https://box.tail74077f.ts.net/mcp"
    urls = endpoint_urls(cfg, "127.0.0.1", 8765)
    assert urls[0] == "https://box.tail74077f.ts.net/mcp" and "http://127.0.0.1:8765/mcp" in urls
    ts = transport_security_for(cfg, "127.0.0.1", 8765)
    assert "box.tail74077f.ts.net" in ts.allowed_hosts and "box.tail74077f.ts.net:*" in ts.allowed_hosts
    cfg.server.public_url = "https://x.example/doc4ai"
    assert canonical_resource(cfg) == "https://x.example/doc4ai/mcp"


def test_public_url_with_prefix_sets_root_path(monkeypatch):
    from aidoc.cli import uvicorn_kwargs
    from aidoc.config import AidocConfig
    cfg = AidocConfig()
    assert uvicorn_kwargs(cfg) == {"proxy_headers": False}
    cfg.server.public_url = "https://x.example/doc4ai"
    cfg.server.trusted_proxies = ["127.0.0.1", "10.0.0.0/8"]
    assert uvicorn_kwargs(cfg) == {"proxy_headers": True, "forwarded_allow_ips": "127.0.0.1,10.0.0.0/8", "root_path": "/doc4ai"}


def test_forwarded_headers_only_from_trusted_proxy(ctx):
    """Behind the configured proxy the gate logs the real client IP and sees https; a stranger's header is ignored."""
    import uvicorn
    from starlette.requests import Request
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    seen = {}

    async def app(scope, receive, send):
        seen["client"], seen["scheme"] = scope["client"], scope["scheme"]
        await send({"type": "http.response.start", "status": 200, "headers": []}); await send({"type": "http.response.body", "body": b""})
    import anyio
    mw = ProxyHeadersMiddleware(app, trusted_hosts=["127.0.0.1"])
    scope = {"type": "http", "client": ("127.0.0.1", 1), "scheme": "http", "headers": [(b"x-forwarded-for", b"203.0.113.9"), (b"x-forwarded-proto", b"https")]}
    anyio.run(mw, scope, None, lambda m: None)
    assert seen["client"][0] == "203.0.113.9" and seen["scheme"] == "https"
    scope["client"] = ("10.9.9.9", 1)
    anyio.run(mw, scope, None, lambda m: None)
    assert seen["client"][0] == "10.9.9.9" and seen["scheme"] == "http"
```

Append to `tests/api/test_serve.py`: `aidoc serve --public-url http://x` → exit 6 with `public_url must be https://`; `GET /api/mcp/status` shows `public_url` and `canonical_resource` when configured.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `uvicorn_kwargs(cfg)` in `cli.py` (pure, used by `_run_uvicorn`); `_check_server()` next to `_check_mcp()`; `canonical_resource()`; Overview card text: 「公開端點 https://…/mcp — 可從網際網路存取；PAT 與 OAuth 皆經 TLS（由 <Funnel / 反向代理> 終結）。」 Write `docs/deploy/https.md` from spec §4.2–4.3 (Funnel: prerequisites, `tailscale funnel --bg --https=443 8765`, `funnel status`, `funnel reset`; Cloudflare named tunnel; Caddy `reverse_proxy localhost:8765` + `trusted_proxies`; nginx `proxy_buffering off`, `proxy_read_timeout 900s`; "never a 3xx to another host"; Anthropic egress; IPv4 A record requirement).
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_public_url.py tests/api/test_serve.py tests/mcp/test_protocol.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(server): public_url with proxy-header trust and a canonical MCP resource URL; HTTPS deployment guide`. **Gate G1/G2:** record the decision before running Funnel on the live server (manual step in B14).

---

### Task B3: `[oauth]` config, schema v6, PRM + AS metadata, test fixtures

**Files:** Modify `src/aidoc/config.py` (`Oauth`, `OauthExternal` dataclasses per spec §10; `_check_oauth()`: `enabled` requires `public_url`; `mode in {"embedded", "external"}`; external requires `issuer`), `aidoc.toml`, `src/aidoc/server/api/settings.py` (ranges; `enabled=true` without https public_url → 422 `public_url_required`), `src/aidoc/store.py` (`SCHEMA_VERSION = 6`, five tables, methods from index §4), `src/aidoc/server/maintenance.py` (`prune_oauth`); create `src/aidoc/oauth/__init__.py`, `src/aidoc/oauth/metadata.py` (router: PRM at `/.well-known/oauth-protected-resource/mcp` and `/.well-known/oauth-protected-resource`, AS metadata at `/.well-known/oauth-authorization-server` and `/.well-known/openid-configuration`; all 404 when `oauth.enabled` is false), `src/aidoc/server/app.py` (include router before `_mount_web`; Envelope passthrough `/oauth/`); create `tests/oauth/__init__.py`, `tests/oauth/conftest.py`; tests `tests/oauth/test_config_schema.py`, `tests/oauth/test_metadata.py`, `tests/api/test_app_basics.py` (schema `"6"`).

`tests/oauth/conftest.py` (sketch): fixture `oauth_ctx` = `ctx` with `cfg.server.public_url = "https://doc4ai.test"`, `cfg.oauth.enabled = True`, `ctx.token = "admin-s3cret"` (consent credential), `ctx.extras["bind_host"] = "127.0.0.1"`; `oauth_client` = `TestClient(create_app(oauth_ctx), base_url="https://doc4ai.test")` (TestClient honours the scheme/host in `base_url`, so `request.url` looks like the public one); helper `pkce()` → `(verifier, challenge)`; helper `cimd_doc(url, redirects)`; `fake_cimd` fixture that monkeypatches `aidoc.oauth.clients.fetch_json(url)` with an in-memory table and `aidoc.oauth.clients.resolve_addrs(host)` to return a public IP; `oauth_live` = `live_server`-style uvicorn for the SDK-client end-to-end test (B8).

- [ ] **Step 1: Write the failing tests**

```python
# tests/oauth/test_config_schema.py
import pytest


def test_oauth_config_rules(tmp_path):
    from aidoc.config import ConfigError, load_config
    p = tmp_path / "aidoc.toml"
    p.write_text('[oauth]\nenabled = true\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="public_url"):
        load_config(p)
    p.write_text('[server]\npublic_url = "https://d.test"\n[oauth]\nenabled = true\nmode = "external"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="issuer"):
        load_config(p)
    p.write_text('[server]\npublic_url = "https://d.test"\n[oauth]\nenabled = true\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.oauth.mode == "embedded" and cfg.oauth.access_ttl_min == 60 and cfg.oauth.refresh_ttl_days == 30
    assert cfg.oauth.refresh_max_days == 90 and cfg.oauth.code_ttl_s == 600 and cfg.oauth.allow_dcr is True
    assert cfg.oauth.dcr_per_hour_per_ip == 10 and cfg.oauth.consent_remember_hours == 12 and cfg.oauth.rate_limit_per_min == 60


def test_schema_v6_tables_and_store_surface(tmp_root):
    import time
    from aidoc.store import Store
    s = Store(tmp_root / "data" / "aidoc.db")
    assert s._q1("SELECT value FROM meta WHERE key='schema_version'")["value"] == "6"
    s.upsert_oauth_client(client_id="https://claude.ai/x", kind="cimd", name="Claude", redirect_uris=["https://claude.ai/api/mcp/auth_callback"], metadata_url="https://claude.ai/x")
    c = s.get_oauth_client("https://claude.ai/x")
    assert c["kind"] == "cimd" and c["redirect_uris"] == ["https://claude.ai/api/mcp/auth_callback"] and c["disabled_at"] is None
    s.create_oauth_code(code_hash="h1", client_id=c["client_id"], redirect_uri=c["redirect_uris"][0], code_challenge="cc", scopes=["doc4ai:read"],
                        resource="https://d.test/mcp", subject="owner", expires_at=time.time() + 600)
    code = s.take_oauth_code("h1")
    assert code["scopes"] == ["doc4ai:read"] and s.take_oauth_code("h1") is None           # single use
    g = s.create_oauth_grant(client_id=c["client_id"], subject="owner", scopes=["doc4ai:read"], resource="https://d.test/mcp", created_ip="1.2.3.4")
    at = s.create_oauth_token(grant_id=g, kind="at", token_hash="at1", prefix="doc4ai_at_abcd", expires_at=time.time() + 60)
    rt = s.create_oauth_token(grant_id=g, kind="rt", token_hash="rt1", prefix="doc4ai_rt_abcd", expires_at=time.time() + 600)
    row = s.get_oauth_token_by_hash("at1")
    assert row["grant_id"] == g and row["scopes"] == ["doc4ai:read"] and row["grant_revoked_at"] is None and row["client_id"] == c["client_id"]
    rt2 = s.rotate_oauth_refresh(rt, new_hash="rt2", new_prefix="doc4ai_rt_efgh", expires_at=time.time() + 600, at=time.time())
    assert s.get_oauth_token_by_hash("rt1")["rotated_at"] and s.get_oauth_token_by_hash("rt2")["parent_id"] == rt
    assert s.revoke_oauth_grant(g, "test", at=time.time()) == 3
    assert s.get_oauth_token_by_hash("at1")["grant_revoked_at"] is not None
    cid = s.create_oauth_consent(cookie_hash="ck", expires_at=time.time() + 100, ip="1.2.3.4")
    assert s.get_oauth_consent_by_hash("ck")["id"] == cid and s.list_oauth_consents()
    pruned = s.prune_oauth(now=time.time() + 10**7)
    assert pruned["codes"] >= 0 and pruned["tokens"] >= 2 and pruned["consents"] == 1
```

```python
# tests/oauth/test_metadata.py
def test_prm_and_as_metadata(oauth_client):
    prm = oauth_client.get("/.well-known/oauth-protected-resource/mcp")
    assert prm.status_code == 200 and "workspace" not in prm.json()
    assert prm.json()["resource"] == "https://doc4ai.test/mcp" and prm.json()["authorization_servers"] == ["https://doc4ai.test"]
    assert prm.json()["scopes_supported"] == ["doc4ai:read"] and prm.json()["bearer_methods_supported"] == ["header"]
    assert oauth_client.get("/.well-known/oauth-protected-resource").json() == prm.json()
    m = oauth_client.get("/.well-known/oauth-authorization-server").json()
    assert m["issuer"] == "https://doc4ai.test" and m["authorization_endpoint"] == "https://doc4ai.test/oauth/authorize"
    assert m["token_endpoint"] == "https://doc4ai.test/oauth/token" and m["registration_endpoint"] == "https://doc4ai.test/oauth/register"
    assert m["revocation_endpoint"] == "https://doc4ai.test/oauth/revoke"
    assert m["response_types_supported"] == ["code"] and m["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert m["code_challenge_methods_supported"] == ["S256"] and m["token_endpoint_auth_methods_supported"] == ["none"]
    assert m["client_id_metadata_document_supported"] is True and m["authorization_response_iss_parameter_supported"] is True
    assert "offline_access" not in m["scopes_supported"] and set(m["scopes_supported"]) == {"doc4ai:read", "doc4ai:convert", "doc4ai:convert:local", "doc4ai:manage"}
    assert oauth_client.get("/.well-known/openid-configuration").json() == m
    assert prm.headers["cache-control"].startswith("public, max-age=")


def test_metadata_404_when_disabled_and_dcr_hidden_when_off(oauth_client, oauth_ctx):
    oauth_ctx.config.oauth.allow_dcr = False
    assert "registration_endpoint" not in oauth_client.get("/.well-known/oauth-authorization-server").json()
    oauth_ctx.config.oauth.enabled = False
    for p in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
        assert oauth_client.get(p).status_code == 404


def test_prefix_public_url_moves_well_known(ctx):
    from fastapi.testclient import TestClient
    from aidoc.server.app import create_app
    ctx.config.server.public_url = "https://x.example/doc4ai"; ctx.config.oauth.enabled = True; ctx.token = "t"
    with TestClient(create_app(ctx), base_url="https://x.example", root_path="/doc4ai") as c:
        prm = c.get("/.well-known/oauth-protected-resource/doc4ai/mcp").json()
        assert prm["resource"] == "https://x.example/doc4ai/mcp" and prm["authorization_servers"] == ["https://x.example/doc4ai"]
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Metadata is computed from `cfg.server.public_url` (issuer = public_url without trailing slash; path-inserted PRM route registered for both `/mcp` and `{prefix}/mcp` — simplest: one route `/.well-known/oauth-protected-resource/{path:path}` that accepts `mcp` or `<prefix>/mcp`, else 404); `Cache-Control: public, max-age=300`. Store methods per index §4 (joins via `oauth_tokens JOIN oauth_grants`); `take_oauth_code` in `BEGIN IMMEDIATE`. `app.py`: `app.include_router(oauth_metadata_router)` before `_mount_web`; Envelope passthrough `/oauth/`.
- [ ] **Step 4: Run** `uv run pytest tests/oauth tests/api/test_app_basics.py tests/mcp/test_store_v4.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(oauth): [oauth] config, schema v6, protected-resource and authorization-server metadata`.

---

### Task B4: Token formats, redaction, issue/verify helpers

**Files:** Create `src/aidoc/oauth/tokens.py` (`AT_PREFIX = "doc4ai_at_"`, `RT_PREFIX = "doc4ai_rt_"`, `CODE_PREFIX = "doc4ai_ac_"`, `CLIENT_PREFIX = "doc4ai_cl_"`; `generate(prefix) -> str` (prefix + 43 base62 + `_` + CRC6, reusing `aidoc.mcp.tokens.b62encode/crc_b62`), `is_well_formed(prefix, raw)`, `hash_(secret, raw)` = `aidoc.mcp.tokens.token_hash`, `display_prefix(raw)`); modify `src/aidoc/mcp/redact.py` (regexes for the four new prefixes → `doc4ai_<kind>_<redacted:abcd>`; form/query values of `code`, `refresh_token`, `code_verifier`, `client_secret` → `<redacted>`; applies to log records, access log lines, `args_summary`, SSE payloads); tests `tests/oauth/test_tokens_redaction.py`.

- [ ] **Step 1: Tests** — format/CRC for each prefix; `is_well_formed` rejects a PAT for `AT_PREFIX`; redaction of `Authorization: Bearer doc4ai_at_…`, of `grant_type=authorization_code&code=doc4ai_ac_…&code_verifier=abc` (both values), of `refresh_token=doc4ai_rt_…`, inside JSON bodies and in a uvicorn access-log line (`POST /oauth/token?code=…` — we never accept query params there, but the redactor must still hide them); PAT redaction unchanged (`tests/mcp/test_redaction.py` stays green).
- [ ] **Step 2–5:** RED → implement → GREEN → commit `feat(oauth): token formats and redaction for codes, access and refresh tokens`.

---

### Task B5: Client registration — CIMD fetcher with SSRF guard, DCR, manual

**Files:** Create `src/aidoc/oauth/clients.py` (`class ClientRegistry`: `resolve(client_id, *, now) -> dict` (manual/DCR row, or CIMD fetch+cache), `fetch_cimd(url) -> dict` (rules spec §5.4), `register_dcr(body, ip) -> dict` (RFC 7591 subset; `InvalidClientMetadata` → 400 `{"error": "invalid_client_metadata", "error_description"}`), `redirect_allowed(client, redirect_uri) -> bool` (exact; loopback ignores port), `resolve_addrs(host) -> list[str]` (DNS; injectable), `fetch_json(url, *, max_bytes, timeout)` (httpx, no redirects; injectable)); `src/aidoc/oauth/endpoints.py` (`POST /oauth/register` only in this task); tests `tests/oauth/test_clients.py`.

- [ ] **Step 1: Write the failing tests**

```python
import json
import time

import pytest

GOOD = {"client_id": "https://claude.ai/oauth/claude-code-client-metadata", "client_name": "Claude Code",
        "redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"], "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"]}


def test_cimd_fetch_rules(fake_cimd, oauth_ctx):
    from aidoc.oauth.clients import ClientRegistry, CimdError
    reg = ClientRegistry(oauth_ctx)
    fake_cimd.put(GOOD["client_id"], GOOD, headers={"Cache-Control": "max-age=3600"})
    c = reg.resolve(GOOD["client_id"], now=time.time())
    assert c["kind"] == "cimd" and c["name"] == "Claude Code" and fake_cimd.calls == 1
    reg.resolve(GOOD["client_id"], now=time.time() + 60)
    assert fake_cimd.calls == 1                                             # cached
    reg.resolve(GOOD["client_id"], now=time.time() + 4000)
    assert fake_cimd.calls == 2                                             # expired → refetched
    for bad_id in ("http://claude.ai/x", "https://claude.ai", "https://u:p@claude.ai/x", "https://claude.ai/x#f", "https://claude.ai/x?q=1"):
        with pytest.raises(CimdError):
            reg.resolve(bad_id, now=time.time())
    fake_cimd.put("https://evil.test/m", {**GOOD, "client_id": "https://other.test/m"})
    with pytest.raises(CimdError, match="client_id"):                        # document must echo its own URL
        reg.resolve("https://evil.test/m", now=time.time())
    fake_cimd.put("https://secret.test/m", GOOD | {"client_id": "https://secret.test/m", "token_endpoint_auth_method": "client_secret_basic"})
    with pytest.raises(CimdError, match="auth_method"):
        reg.resolve("https://secret.test/m", now=time.time())


def test_cimd_blocks_private_and_redirect(fake_cimd, oauth_ctx):
    from aidoc.oauth.clients import ClientRegistry, CimdError
    reg = ClientRegistry(oauth_ctx)
    for ip in ("127.0.0.1", "10.1.2.3", "192.168.0.5", "169.254.1.1", "100.64.3.3", "::1", "fd00::1"):
        fake_cimd.put("https://priv.test/m", GOOD | {"client_id": "https://priv.test/m"}, addrs=[ip])
        with pytest.raises(CimdError, match="address"):
            reg.resolve("https://priv.test/m", now=time.time())
    fake_cimd.put("https://redir.test/m", None, status=302, headers={"Location": "https://claude.ai/x"})
    with pytest.raises(CimdError, match="redirect"):
        reg.resolve("https://redir.test/m", now=time.time())
    fake_cimd.put("https://big.test/m", {"client_id": "https://big.test/m", "pad": "x" * 70000, "redirect_uris": ["https://a/b"], "client_name": "b"})
    with pytest.raises(CimdError, match="64"):
        reg.resolve("https://big.test/m", now=time.time())
    fake_cimd.put("https://err.test/m", None, status=500)
    with pytest.raises(CimdError):
        reg.resolve("https://err.test/m", now=time.time())
    assert reg.cache_size == 0 or "https://err.test/m" not in reg.cached_ids()  # errors are not cached
    for _ in range(7):
        try: reg.resolve("https://err.test/m", now=time.time())
        except CimdError: pass
    assert fake_cimd.calls_for("https://err.test/m") <= 6                   # per-client fetch rate limit 6/min


def test_redirect_matching_rules(oauth_ctx):
    from aidoc.oauth.clients import redirect_allowed
    c = {"redirect_uris": ["https://claude.ai/api/mcp/auth_callback", "http://localhost/callback", "http://127.0.0.1/callback"]}
    assert redirect_allowed(c, "https://claude.ai/api/mcp/auth_callback")
    assert not redirect_allowed(c, "https://claude.ai/api/mcp/auth_callback/") and not redirect_allowed(c, "https://claude.ai/api/mcp/auth_callback?x=1")
    assert redirect_allowed(c, "http://localhost:51234/callback") and redirect_allowed(c, "http://127.0.0.1:8787/callback")
    assert not redirect_allowed(c, "http://localhost:51234/other") and not redirect_allowed(c, "http://evil.local:51234/callback")


def test_dcr_register_and_limits(oauth_client, oauth_ctx):
    body = {"redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"], "client_name": "ChatGPT", "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "software_id": "chatgpt"}
    r = oauth_client.post("/oauth/register", json=body)
    assert r.status_code == 201 and r.json()["client_id"].startswith("doc4ai_cl_") and r.json()["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in r.json() and r.json()["redirect_uris"] == body["redirect_uris"]
    row = oauth_ctx.store.get_oauth_client(r.json()["client_id"])
    assert row["kind"] == "dcr" and row["software_id"] == "chatgpt"
    bad = oauth_client.post("/oauth/register", json={**body, "redirect_uris": ["http://evil.example/cb"]})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_redirect_uri"
    bad = oauth_client.post("/oauth/register", json={**body, "token_endpoint_auth_method": "client_secret_post"})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_client_metadata"
    for _ in range(10):
        oauth_client.post("/oauth/register", json=body)
    assert oauth_client.post("/oauth/register", json=body).status_code == 429
    oauth_ctx.config.oauth.allow_dcr = False
    assert oauth_client.post("/oauth/register", json=body).status_code == 404


def test_manual_client(oauth_ctx):
    from aidoc.oauth.clients import ClientRegistry
    oauth_ctx.store.upsert_oauth_client(client_id="my-ide", kind="manual", name="My IDE", redirect_uris=["http://localhost/cb"])
    assert ClientRegistry(oauth_ctx).resolve("my-ide", now=time.time())["kind"] == "manual"
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `fetch_cimd`: parse URL (`https`, non-empty path, no userinfo/query/fragment); `resolve_addrs(host)` → every address must pass `_is_public(ip)` (`not (is_private or is_loopback or is_link_local or is_reserved or is_multicast or is_unspecified or in 100.64.0.0/10)`); `fetch_json` with `httpx.Client(follow_redirects=False, timeout=5)`, 3xx → error, non-200 → error, `Content-Type` must contain `json`, stream with a 64 KB cap; validate `client_id == url`, `redirect_uris` list of str non-empty, `client_name` str, `token_endpoint_auth_method in (None, "none")`; cache in memory `{client_id: (doc, until)}` with `until = now + clamp(max-age, 60, 86400)`; per-client fetch limiter (6/min); persist via `upsert_oauth_client(kind="cimd", metadata_json=…, cache_until=…)` and `touch_oauth_client`. DCR: validate per spec §5.4; `client_id = generate(CLIENT_PREFIX)`; IP rate limit via a small token bucket (reuse `aidoc.mcp.ratelimit.RateLimiter` keyed by `f"dcr:{ip}"` with `per_min = dcr_per_hour_per_ip / 60` → simpler: a fixed-window counter in `ClientRegistry`); response 201 per RFC 7591 (`client_id_issued_at`, echo of accepted metadata). `redirect_allowed`: exact string compare, except when both the registered and the presented URI are `http://localhost|127.0.0.1|[::1]` — compare scheme+host+path ignoring port.
- [ ] **Step 4: Run** `uv run pytest tests/oauth -q` → PASS.
- [ ] **Step 5: Commit** `feat(oauth): client registration — CIMD with SSRF guard and cache, DCR, manual clients`.

---

### Task B6: Authorization codes, grants, `/oauth/token`, `/oauth/revoke`

**Files:** Create `src/aidoc/oauth/grants.py` (`class GrantService`: `issue_code(client, redirect_uri, code_challenge, scopes, resource, ip) -> str`, `exchange_code(form, ip) -> TokenResponse`, `refresh(form, ip) -> TokenResponse`, `revoke(token_raw) -> None`, `verify_pkce(verifier, challenge) -> bool`; errors `OAuthError(error, description, status=400)`), modify `src/aidoc/oauth/endpoints.py` (`POST /oauth/token`, `POST /oauth/revoke`; form-urlencoded only; `Cache-Control: no-store`, `Pragma: no-cache`), `src/aidoc/server/maintenance.py` (`prune_oauth` in `run_once`); tests `tests/oauth/test_token_endpoint.py`.

- [ ] **Step 1: Write the failing tests**

```python
import base64
import hashlib
import secrets
import time

import pytest

RES = "https://doc4ai.test/mcp"


def pkce():
    v = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, c


@pytest.fixture
def client_row(oauth_ctx):
    oauth_ctx.store.upsert_oauth_client(client_id="https://claude.ai/cimd", kind="cimd", name="Claude",
                                        redirect_uris=["https://claude.ai/api/mcp/auth_callback", "http://localhost/callback"])
    return oauth_ctx.store.get_oauth_client("https://claude.ai/cimd")


def issue(oauth_ctx, client_row, challenge, scopes=("doc4ai:read",), redirect="https://claude.ai/api/mcp/auth_callback", resource=RES):
    from aidoc.oauth.grants import GrantService
    return GrantService(oauth_ctx).issue_code(client_row, redirect, challenge, list(scopes), resource, "1.2.3.4")


def token(oauth_client, **form):
    return oauth_client.post("/oauth/token", data=form)


def test_code_exchange_issues_rotating_tokens(oauth_client, oauth_ctx, client_row):
    v, c = pkce()
    code = issue(oauth_ctx, client_row, c)
    assert code.startswith("doc4ai_ac_")
    r = token(oauth_client, grant_type="authorization_code", code=code, code_verifier=v, redirect_uri="https://claude.ai/api/mcp/auth_callback",
              client_id=client_row["client_id"], resource=RES)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
    body = r.json()
    assert body["token_type"] == "Bearer" and body["access_token"].startswith("doc4ai_at_") and body["refresh_token"].startswith("doc4ai_rt_")
    assert body["expires_in"] == 3600 and body["scope"] == "doc4ai:read"
    assert oauth_ctx.store.get_oauth_token_by_hash(__import__("aidoc.oauth.tokens", fromlist=["hash_"]).hash_(oauth_ctx.extras["mcp"].secret, body["access_token"]))
    r2 = token(oauth_client, grant_type="refresh_token", refresh_token=body["refresh_token"], client_id=client_row["client_id"])
    assert r2.status_code == 200 and r2.json()["refresh_token"] != body["refresh_token"] and r2.json()["access_token"] != body["access_token"]
    # replay of the rotated refresh token kills the whole grant (RFC 9700 §4.14.2)
    r3 = token(oauth_client, grant_type="refresh_token", refresh_token=body["refresh_token"], client_id=client_row["client_id"])
    assert r3.status_code == 400 and r3.json()["error"] == "invalid_grant"
    r4 = token(oauth_client, grant_type="refresh_token", refresh_token=r2.json()["refresh_token"], client_id=client_row["client_id"])
    assert r4.status_code == 400 and r4.json()["error"] == "invalid_grant"
    assert oauth_ctx.store.list_oauth_grants(include_revoked=True)[0]["revoked_reason"] == "refresh_replay"


def test_code_reuse_revokes_grant(oauth_client, oauth_ctx, client_row):
    v, c = pkce()
    code = issue(oauth_ctx, client_row, c)
    common = dict(grant_type="authorization_code", code=code, code_verifier=v, redirect_uri="https://claude.ai/api/mcp/auth_callback", client_id=client_row["client_id"], resource=RES)
    first = token(oauth_client, **common).json()
    again = token(oauth_client, **common)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    assert token(oauth_client, grant_type="refresh_token", refresh_token=first["refresh_token"], client_id=client_row["client_id"]).status_code == 400


@pytest.mark.parametrize("mutate,error", [
    (lambda f, v, c: f.update(code_verifier="wrong" * 10), "invalid_grant"),
    (lambda f, v, c: f.update(code_verifier="short"), "invalid_grant"),                       # RFC 7636 length
    (lambda f, v, c: f.update(redirect_uri="https://claude.ai/other"), "invalid_grant"),
    (lambda f, v, c: f.update(client_id="https://other.ai/cimd"), "invalid_grant"),
    (lambda f, v, c: f.update(resource="https://doc4ai.test/other"), "invalid_target"),
    (lambda f, v, c: f.pop("resource"), "invalid_target"),
    (lambda f, v, c: f.update(grant_type="client_credentials"), "unsupported_grant_type"),
])
def test_token_request_errors(oauth_client, oauth_ctx, client_row, mutate, error):
    v, c = pkce()
    code = issue(oauth_ctx, client_row, c)
    form = dict(grant_type="authorization_code", code=code, code_verifier=v, redirect_uri="https://claude.ai/api/mcp/auth_callback", client_id=client_row["client_id"], resource=RES)
    mutate(form, v, c)
    r = token(oauth_client, **form)
    assert r.status_code == 400 and r.json()["error"] == error, r.text


def test_code_expires_and_json_body_rejected(oauth_client, oauth_ctx, client_row, monkeypatch):
    v, c = pkce()
    code = issue(oauth_ctx, client_row, c)
    oauth_ctx.store._update("oauth_codes", "code_hash", __import__("aidoc.oauth.tokens", fromlist=["hash_"]).hash_(oauth_ctx.extras["mcp"].secret, code),
                            {"expires_at": time.time() - 1})
    r = token(oauth_client, grant_type="authorization_code", code=code, code_verifier=v, redirect_uri="https://claude.ai/api/mcp/auth_callback", client_id=client_row["client_id"], resource=RES)
    assert r.json()["error"] == "invalid_grant"
    r = oauth_client.post("/oauth/token", json={"grant_type": "authorization_code"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"


def test_pat_is_not_an_oauth_credential(oauth_client, oauth_ctx, client_row):
    from aidoc.mcp import tokens as T
    raw = T.generate_token()
    r = token(oauth_client, grant_type="refresh_token", refresh_token=raw, client_id=client_row["client_id"])
    assert r.status_code == 400 and r.json() == {"error": "invalid_grant", "error_description": "refresh token is invalid, expired or revoked"}


def test_refresh_scope_narrowing_and_lifetimes(oauth_client, oauth_ctx, client_row):
    v, c = pkce()
    code = issue(oauth_ctx, client_row, c, scopes=("doc4ai:read", "doc4ai:convert"))
    first = token(oauth_client, grant_type="authorization_code", code=code, code_verifier=v, redirect_uri="https://claude.ai/api/mcp/auth_callback", client_id=client_row["client_id"], resource=RES).json()
    r = token(oauth_client, grant_type="refresh_token", refresh_token=first["refresh_token"], client_id=client_row["client_id"], scope="doc4ai:read")
    assert r.json()["scope"] == "doc4ai:read"
    r = token(oauth_client, grant_type="refresh_token", refresh_token=r.json()["refresh_token"], client_id=client_row["client_id"], scope="doc4ai:read doc4ai:manage")
    assert r.status_code == 400 and r.json()["error"] == "invalid_scope"
    rt = oauth_ctx.store.get_oauth_token_by_hash(__import__("aidoc.oauth.tokens", fromlist=["hash_"]).hash_(oauth_ctx.extras["mcp"].secret, first["refresh_token"]))
    assert 29 * 86400 < rt["expires_at"] - rt["created_at"] <= 30 * 86400


def test_revoke_endpoint_always_200(oauth_client, oauth_ctx, client_row):
    v, c = pkce()
    code = issue(oauth_ctx, client_row, c)
    body = token(oauth_client, grant_type="authorization_code", code=code, code_verifier=v, redirect_uri="https://claude.ai/api/mcp/auth_callback", client_id=client_row["client_id"], resource=RES).json()
    assert oauth_client.post("/oauth/revoke", data={"token": body["access_token"]}).status_code == 200
    assert oauth_client.post("/oauth/revoke", data={"token": "garbage"}).status_code == 200
    assert token(oauth_client, grant_type="refresh_token", refresh_token=body["refresh_token"], client_id=client_row["client_id"]).json()["error"] == "invalid_grant"
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** per spec §5.5 steps 3–7 and index M6/M7. `issue_code` stores `HMAC(secret, code)`; `exchange_code`: `take_oauth_code(hash)` (None → `invalid_grant`; **if a grant_id is already attached → `revoke_oauth_grant(reason="code_reuse")`**: to support that, `take_oauth_code` returns the row even when consumed if we keep a `consumed_at` instead of deleting — amend the Store method: `take_oauth_code` marks `consumed_at` and sets `grant_id`; a second take sees `consumed_at` and the service revokes; `prune_oauth` deletes consumed/expired codes after 1 h), check expiry, `client_id`, `redirect_uri` equality, `resource` equality (missing/mismatch → `invalid_target`), verifier length 43–128 and charset, `hmac.compare_digest(b64url(sha256(verifier)), challenge)`; create grant + AT (`access_ttl_min`) + RT (`min(now + refresh_ttl_days, grant.created_at + refresh_max_days)`). `refresh`: lookup RT by hash → missing / wrong kind / expired / revoked → `invalid_grant`; `rotated_at` set → **replay**: `revoke_oauth_grant(reason="refresh_replay")` → `invalid_grant`; `client_id` mismatch → `invalid_grant`; `resource` given and ≠ grant → `invalid_target`; `scope` ⊄ grant → `invalid_scope`; rotate. Token endpoint: `Content-Type` must be `application/x-www-form-urlencoded` (else `invalid_request`); errors as `{"error", "error_description"}` with 400 (RFC 6749 §5.2) and `Cache-Control: no-store`. `revoke`: hash lookup across AT/RT → `revoke_oauth_grant(reason="client_revoke")`; always 200 `{}`. Failed exchanges count into `AuthFailureGuard` (per IP) → 429 after the flood limit.
- [ ] **Step 4: Run** `uv run pytest tests/oauth -q` → PASS.
- [ ] **Step 5: Commit** `feat(oauth): authorization-code exchange with PKCE, resource binding, rotating refresh tokens and revocation`.

---

### Task B7: `/oauth/authorize` and the consent screen

**Files:** Create `src/aidoc/oauth/consent.py` (`render_consent(ctx_vars) -> str` from an inline HTML template with the CSP/`X-Frame-Options` headers; `sign_form_nonce(secret, state, exp)` / `check_form_nonce`; cookie helpers `issue_remember_cookie` / `check_remember_cookie` using `HMAC(server_secret, sha256(server_token) + cookie_id)`), modify `src/aidoc/oauth/endpoints.py` (`GET /oauth/authorize`, `POST /oauth/authorize`); tests `tests/oauth/test_authorize.py`.

- [ ] **Step 1: Write the failing tests**

```python
from urllib.parse import parse_qs, urlparse

import pytest

from tests.oauth.test_token_endpoint import RES, client_row, pkce  # noqa: F401

AUTH = dict(response_type="code", client_id="https://claude.ai/cimd", redirect_uri="https://claude.ai/api/mcp/auth_callback",
            scope="doc4ai:read doc4ai:convert", state="st4te", code_challenge_method="S256", resource=RES)


def test_consent_page_shows_host_and_scopes(oauth_client, client_row):
    v, c = pkce()
    r = oauth_client.get("/oauth/authorize", params={**AUTH, "code_challenge": c})
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    html = r.text
    assert "claude.ai" in html and "Claude" not in html.split("<title>")[0]        # host shown; the self-reported name is not the headline
    assert 'name="scopes" value="doc4ai:read" checked' in html and 'value="doc4ai:convert" checked' in html
    assert 'value="doc4ai:manage"' in html and 'value="doc4ai:manage" checked' not in html
    assert 'type="password" name="admin_token"' in html and 'name="form_nonce"' in html
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'none'" in r.headers["content-security-policy"]
    assert "<script" not in html and "logo_uri" not in html


def test_bad_redirect_or_client_never_redirects(oauth_client, client_row):
    v, c = pkce()
    r = oauth_client.get("/oauth/authorize", params={**AUTH, "code_challenge": c, "redirect_uri": "https://evil.example/cb"}, follow_redirects=False)
    assert r.status_code == 400 and "location" not in r.headers
    r = oauth_client.get("/oauth/authorize", params={**AUTH, "code_challenge": c, "client_id": "https://nobody.test/m"}, follow_redirects=False)
    assert r.status_code == 400 and "location" not in r.headers


@pytest.mark.parametrize("bad,error", [
    ({"code_challenge_method": "plain"}, "invalid_request"), ({"code_challenge": None}, "invalid_request"),
    ({"state": None}, "invalid_request"), ({"resource": "https://doc4ai.test/other"}, "invalid_target"), ({"response_type": "token"}, "unsupported_response_type"),
])
def test_param_errors_redirect_with_error(oauth_client, client_row, bad, error):
    v, c = pkce()
    params = {**AUTH, "code_challenge": c}
    for k, val in bad.items():
        params.pop(k) if val is None else params.update({k: val})
    r = oauth_client.get("/oauth/authorize", params=params, follow_redirects=False)
    if "state" in bad:
        assert r.status_code == 400                                   # no state → cannot redirect safely
    else:
        assert r.status_code == 302
        q = parse_qs(urlparse(r.headers["location"]).query)
        assert q["error"] == [error] and q["state"] == ["st4te"] and q["iss"] == ["https://doc4ai.test"]


def _consent_post(oauth_client, html, **over):
    import re
    nonce = re.search(r'name="form_nonce" value="([^"]+)"', html).group(1)
    form = {"form_nonce": nonce, "state": "st4te", "admin_token": "admin-s3cret", "scopes": ["doc4ai:read", "doc4ai:convert"], "decision": "allow", **over}
    return oauth_client.post("/oauth/authorize", data=form, headers={"Origin": "https://doc4ai.test"}, follow_redirects=False)


def test_consent_allow_redirects_with_code_and_iss(oauth_client, oauth_ctx, client_row):
    v, c = pkce()
    html = oauth_client.get("/oauth/authorize", params={**AUTH, "code_challenge": c}).text
    r = _consent_post(oauth_client, html)
    assert r.status_code == 302
    loc = urlparse(r.headers["location"]); q = parse_qs(loc.query)
    assert loc.netloc == "claude.ai" and q["state"] == ["st4te"] and q["iss"] == ["https://doc4ai.test"] and q["code"][0].startswith("doc4ai_ac_")
    t = oauth_client.post("/oauth/token", data=dict(grant_type="authorization_code", code=q["code"][0], code_verifier=v,
                                                   redirect_uri=AUTH["redirect_uri"], client_id=AUTH["client_id"], resource=RES))
    assert t.status_code == 200 and t.json()["scope"] == "doc4ai:read doc4ai:convert"
    calls = oauth_ctx.store.list_mcp_calls(limit=5)
    assert any(cl["method"] == "oauth/authorize" and cl["status"] == "ok" for cl in calls)


def test_consent_scope_subset_and_deny(oauth_client, client_row):
    v, c = pkce()
    html = oauth_client.get("/oauth/authorize", params={**AUTH, "code_challenge": c}).text
    r = _consent_post(oauth_client, html, scopes=["doc4ai:read"])
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    t = oauth_client.post("/oauth/token", data=dict(grant_type="authorization_code", code=code, code_verifier=v, redirect_uri=AUTH["redirect_uri"], client_id=AUTH["client_id"], resource=RES))
    assert t.json()["scope"] == "doc4ai:read"
    html = oauth_client.get("/oauth/authorize", params={**AUTH, "code_challenge": c}).text
    r = _consent_post(oauth_client, html, decision="deny")
    assert parse_qs(urlparse(r.headers["location"]).query)["error"] == ["access_denied"]
    html = oauth_client.get("/oauth/authorize", params={**AUTH, "code_challenge": c}).text
    r = _consent_post(oauth_client, html, scopes=[])
    assert r.status_code == 400                                       # at least doc4ai:read


def test_consent_csrf_rejected_and_headers(oauth_client, client_row):
    v, c = pkce()
    html = oauth_client.get("/oauth/authorize", params={**AUTH, "code_challenge": c}).text
    r = _consent_post(oauth_client, html, form_nonce="forged")
    assert r.status_code == 400
    r = oauth_client.post("/oauth/authorize", data={"state": "st4te", "admin_token": "admin-s3cret", "decision": "allow"},
                          headers={"Origin": "https://evil.example"}, follow_redirects=False)
    assert r.status_code == 403


def test_consent_bruteforce_429_and_logged(oauth_client, oauth_ctx, client_row):
    v, c = pkce()
    for _ in range(10):
        html = oauth_client.get("/oauth/authorize", params={**AUTH, "code_challenge": c}).text
        assert _consent_post(oauth_client, html, admin_token="wrong").status_code == 401
    html = oauth_client.get("/oauth/authorize", params={**AUTH, "code_challenge": c}).text
    assert _consent_post(oauth_client, html, admin_token="wrong").status_code == 429
    assert any(cl["method"] == "oauth/authorize" and cl["status"] == "auth_error" for cl in oauth_ctx.store.list_mcp_calls(limit=20))


def test_remember_cookie_skips_token_and_dies_with_admin_token(oauth_client, oauth_ctx, client_row):
    v, c = pkce()
    html = oauth_client.get("/oauth/authorize", params={**AUTH, "code_challenge": c}).text
    r = _consent_post(oauth_client, html, remember="1")
    assert r.status_code == 302 and "doc4ai_consent=" in r.headers.get("set-cookie", "") and "HttpOnly" in r.headers["set-cookie"] and "Secure" in r.headers["set-cookie"]
    html = oauth_client.get("/oauth/authorize", params={**AUTH, "code_challenge": c}).text
    assert 'name="admin_token"' not in html                             # remembered: no password field
    r = _consent_post(oauth_client, html, admin_token="")
    assert r.status_code == 302 and "code=" in r.headers["location"]
    oauth_ctx.token = "rotated-admin-token"                             # server token changed → cookie invalid
    html = oauth_client.get("/oauth/authorize", params={**AUTH, "code_challenge": c}).text
    assert 'name="admin_token"' in html
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `GET`: validate per spec §5.5 step 1 (order: client resolve → redirect_uri match → **only then** error redirects; `state` missing → 400 page); resolve client via `ClientRegistry` (CIMD fetch errors → 400 page naming the reason); render consent (host from `urlparse(client_id).netloc` for CIMD, 「未驗證的 client：<name>」 for DCR, `client_id` for manual); `form_nonce = HMAC(secret, f"{state}|{exp}") + "." + exp` (10 min). `POST`: `Origin`/`Sec-Fetch-Site` same-origin check (403); nonce check (400); `remember` cookie check **or** `hmac.compare_digest(admin_token, ctx.token)` (401 + `AuthFailureGuard.on_failure(ip, "consent", …)` → 429); scopes ⊆ requested, ≥ `doc4ai:read` (400); `decision=deny` → `error=access_denied`; else `GrantService.issue_code(...)` → 302 with `code`, `state`, `iss`; `Set-Cookie: doc4ai_consent=<id>.<sig>; Path=/oauth; HttpOnly; Secure; SameSite=Lax; Max-Age=<consent_remember_hours*3600>` and `create_oauth_consent(cookie_hash=sha256(cookie))`. Record `mcp_calls` rows (`method="oauth/authorize"`, `status ok|auth_error`, `args_summary={client_host, scopes}`) and SSE `mcp.oauth {action: "granted"}`. The HTML template: Traditional Chinese copy from spec §7.1; `CSP: default-src 'none'; style-src 'unsafe-inline'; form-action 'self'`; `X-Frame-Options: DENY`; `Referrer-Policy: no-referrer`.
- [ ] **Step 4: Run** `uv run pytest tests/oauth -q` → PASS.
- [ ] **Step 5: Commit** `feat(oauth): /oauth/authorize with a server-token consent screen, CSRF nonce, remember cookie and iss`.

---

### Task B8: Verifier chain, 401 with `resource_metadata`, step-up `_meta`, external JWT branch

**Files:** Modify `src/aidoc/mcp/principal.py` (`class OAuthTokenVerifier(store, secret, canonical)`, `class JwtVerifier(cfg, canonical, jwks_client)`, `class VerifierChain(pat, oauth, jwt, cfg)` with `verify(raw, now=None) -> Principal | AuthFailure`; `FailureReason` gains `"wrong_audience"`), `src/aidoc/mcp/server.py` (`build_mcp` wires the chain; `McpGate` receives it), `src/aidoc/mcp/gate.py` (401 passes `resource_metadata = f"{public_url}/.well-known/oauth-protected-resource/mcp"` when `cfg.oauth.enabled`; failure text for `wrong_audience`), `src/aidoc/compat/auth.py` (same chain + `resource_metadata`, if the compat plan landed), `src/aidoc/mcp/registry.py` (`failure_result` adds `_meta={"mcp/www_authenticate": [...]}` for `forbidden_scope` when the principal is OAuth), `src/aidoc/mcp/calllog.py` (`touch_token` for OAuth updates `oauth_tokens.last_used_at` instead); tests `tests/oauth/test_verifier_chain.py`, `tests/mcp/conftest.py` (parametrise `mcp_env` over `oauth.enabled` via an env var `AIDOC_TEST_OAUTH=1` so the whole `tests/mcp` suite runs in both modes in CI — add a second CI job).

- [ ] **Step 1: Write the failing tests**

```python
import time

import jwt
import pytest

from tests.mcp.conftest import mcp_call, mcp_list_tools, raw_post
from tests.oauth.test_token_endpoint import RES, client_row, pkce  # noqa: F401


def _access_token(oauth_ctx, client_row, scopes=("doc4ai:read",)):
    from aidoc.oauth.grants import GrantService
    svc = GrantService(oauth_ctx)
    v, c = pkce()
    code = svc.issue_code(client_row, "https://claude.ai/api/mcp/auth_callback", c, list(scopes), RES, "1.1.1.1")
    tr = svc.exchange_code({"grant_type": "authorization_code", "code": code, "code_verifier": v, "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                            "client_id": client_row["client_id"], "resource": RES}, "1.1.1.1")
    return tr.access_token, tr.refresh_token


def test_chain_dispatches_by_prefix(oauth_ctx, client_row):
    from aidoc.mcp.principal import AuthFailure, VerifierChain
    chain = oauth_ctx.extras["mcp"].verifier
    assert isinstance(chain, VerifierChain)
    at, _ = _access_token(oauth_ctx, client_row)
    p = chain.verify(at)
    assert p.kind == "oauth" and p.client_id == client_row["client_id"] and p.subject == "owner" and p.scopes == frozenset({"doc4ai:read"})
    assert p.name == "claude.ai（OAuth）" and p.token_id and p.rate_limit_per_min == oauth_ctx.config.oauth.rate_limit_per_min
    assert chain.verify("doc4ai_rt_" + "x" * 50).reason == "bad_format"                   # a refresh token is not a bearer credential
    assert chain.verify("eyJhbGciOiJSUzI1NiJ9.e30.sig").reason == "bad_format"             # no external mode → JWTs rejected
    from aidoc.mcp import tokens as T
    assert isinstance(chain.verify(T.generate_token()), AuthFailure)                     # PAT path still answers (unknown_token)


def test_oauth_token_on_mcp_and_revocation(oauth_ctx, oauth_live, client_row):
    at, rt = _access_token(oauth_ctx, client_row, scopes=("doc4ai:read",))
    tools = mcp_list_tools(oauth_live, at)
    assert {t.name for t in tools.tools} >= {"search_library", "read_document"} and "convert_document" not in {t.name for t in tools.tools}
    row = oauth_ctx.store.list_mcp_calls(limit=1)[0]
    assert row["token_id"] and row["method"] == "tools/list"
    oauth_ctx.store.revoke_oauth_grant(oauth_ctx.store.list_oauth_grants()[0]["id"], "admin", at=time.time())
    r = raw_post(oauth_live, at, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert r.status_code == 401
    www = r.headers["www-authenticate"]
    assert 'error="invalid_token"' in www and 'resource_metadata="https://doc4ai.test/.well-known/oauth-protected-resource/mcp"' in www


def test_401_shape_without_oauth_is_unchanged(mcp_env, mcp_server):
    r = raw_post(mcp_server, "doc4ai_pat_" + "x" * 50, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert r.status_code == 401 and "resource_metadata" not in r.headers["www-authenticate"]


def test_forbidden_scope_carries_www_authenticate_meta_for_oauth(oauth_ctx, oauth_live, client_row):
    at, _ = _access_token(oauth_ctx, client_row, scopes=("doc4ai:read",))
    res = mcp_call(oauth_live, at, "convert_document", {"filename": "a.pdf", "content_base64": "JVBERi0="})
    assert res.is_error and res.structured_content["code"] == "forbidden_scope"
    meta = res.meta["mcp/www_authenticate"][0]
    assert 'error="insufficient_scope"' in meta and 'scope="doc4ai:convert"' in meta and "resource_metadata=" in meta


def test_external_jwt_branch(oauth_ctx, oauth_live, rsa_jwks):
    """mode=external: the server is a pure resource server; tokens come from an IdP we only know by issuer + JWKS."""
    oauth_ctx.config.oauth.mode = "external"
    oauth_ctx.config.oauth.external.issuer = rsa_jwks.issuer
    oauth_ctx.config.oauth.external.jwks_uri = rsa_jwks.jwks_uri
    oauth_ctx.extras["mcp"].verifier.reload()
    good = rsa_jwks.sign({"iss": rsa_jwks.issuer, "aud": RES, "sub": "u1", "client_id": "idp-client", "scope": "doc4ai:read", "exp": time.time() + 300, "iat": time.time(), "jti": "1"}, typ="at+jwt")
    assert len(mcp_list_tools(oauth_live, good).tools) > 0
    for claims, reason in [({"aud": "https://other/mcp"}, "wrong_audience"), ({"exp": time.time() - 120}, "expired"), ({"iss": "https://evil"}, "unknown_token")]:
        bad = rsa_jwks.sign({"iss": rsa_jwks.issuer, "aud": RES, "sub": "u1", "scope": "doc4ai:read", "exp": time.time() + 300, **claims})
        r = raw_post(oauth_live, bad, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        assert r.status_code == 401, reason
    none_alg = jwt.encode({"iss": rsa_jwks.issuer, "aud": RES, "sub": "u1", "exp": time.time() + 300}, key="", algorithm="none")
    assert raw_post(oauth_live, none_alg, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).status_code == 401
    skew = rsa_jwks.sign({"iss": rsa_jwks.issuer, "aud": RES, "sub": "u1", "scope": "doc4ai:read", "exp": time.time() - 30})   # within 60 s skew
    assert len(mcp_list_tools(oauth_live, skew).tools) > 0


def test_disable_kills_tokens(oauth_ctx, oauth_live, client_row):
    at, _ = _access_token(oauth_ctx, client_row)
    assert len(mcp_list_tools(oauth_live, at).tools) > 0
    oauth_ctx.config.oauth.enabled = False
    assert raw_post(oauth_live, at, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).status_code == 401
```

(`rsa_jwks` fixture in `tests/oauth/conftest.py`: generates an RSA key with `cryptography`, serves `/.well-known/jwks.json` from a tiny `live_server`-style app, exposes `issuer`, `jwks_uri`, `sign(claims, typ=None)`.)

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `OAuthTokenVerifier.verify`: `is_well_formed(AT_PREFIX, raw)` → hash lookup → `kind == "at"`, not revoked (token or grant), not expired, `resource == canonical` (else `wrong_audience`), client not disabled → `Principal(kind="oauth", subject=row["subject"], token_id=row["id"], client_id=row["client_id"], scopes=frozenset(row["scopes"]), expires_at=row["expires_at"], name=f"{urlparse(client_id).netloc or client_id}（OAuth）", rate_limit_per_min=cfg.oauth.rate_limit_per_min)`; when `cfg.oauth.enabled` is false → `AuthFailure("unknown_token")`. `JwtVerifier`: `jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600)`; `jwt.decode(raw, key, algorithms=["RS256", "ES256"], audience=canonical, issuer=issuer, leeway=60, options={"require": ["exp", "iss", "aud"]})`; map `InvalidAudienceError → wrong_audience`, `ExpiredSignatureError → expired`, anything else → `unknown_token`; scopes from `scope` (split) or `scp` (list); `client_id` from `client_id`/`azp`. `VerifierChain.verify`: prefix dispatch per index D8; `reload()` rebuilds the JWT branch after a config change. `McpGate`: `www_authenticate(..., resource_metadata=prm_url if cfg.oauth.enabled else None)`; `_FAILURE_TEXT["wrong_audience"] = "This token was issued for another resource"`. `registry.failure_result(f, principal)`: when `f.code == "forbidden_scope"` and `principal.kind == "oauth"` → `_meta={"mcp/www_authenticate": [f'Bearer error="insufficient_scope", scope="{scope}", resource_metadata="{prm}"']}` (the `CallToolResult` model accepts `meta`). `CallRecorder.touch_token`: `token_id` belonging to `oauth_tokens` → `touch_oauth_token`; detect by prefix of the stored id (OAuth ids are `uuid4().hex` too — store `kind` on the `Principal` instead: `touch_token(principal, …)`; keep the old signature as a thin wrapper).
- [ ] **Step 4: Run** `uv run pytest tests/oauth tests/mcp -q` and `AIDOC_TEST_OAUTH=1 uv run pytest tests/mcp -q` → PASS.
- [ ] **Step 5: Commit** `feat(mcp): verifier chain (PAT, embedded OAuth, external JWT), 401 with resource_metadata, step-up metadata`.

---

### Task B9: OAuth client identity in `mcp_clients`, call rows, maintenance

**Files:** Modify `src/aidoc/mcp/calllog.py` (`note_client` for OAuth principals: `token_id = "oauth:" + client_id` (index M1), `oauth_client_id` filled), `src/aidoc/server/api/mcp.py` (`serialize_client`/`serialize_call` resolve names: `token_name = "<host>（OAuth）"`, `token_status` from the grant), `src/aidoc/server/maintenance.py` (`prune_oauth` + `prune_oauth_clients(dcr_unused_before=now-30d)`); tests `tests/oauth/test_identity_admin.py`.

- [ ] **Step 1: Tests** — after one OAuth `tools/list`: `/api/mcp/clients` shows a client with `token_name == "claude.ai（OAuth）"`, `oauth_client_id`, `protocol_version`; `/api/mcp/calls` rows carry `token_name`; `/api/mcp/tokens` does **not** list OAuth tokens (they live in the OAuth tab); revoking the grant flips the client's `token_status` to `revoked` on the `mcp.oauth` event; maintenance prunes an expired code, an expired token and a DCR client with no grant older than 30 days, but never a CIMD client.
- [ ] **Step 2–5:** RED → implement → GREEN → commit `feat(mcp): OAuth clients and calls in the admin data; OAuth housekeeping`.

---

### Task B10: Admin API `/api/mcp/oauth/*` and SSE `mcp.oauth`

**Files:** Modify `src/aidoc/server/api/mcp.py` (routes per spec §7.2: `GET oauth/status` with self-checks, `GET/POST oauth/clients`, `POST oauth/clients/{id}/disable|enable`, `GET oauth/grants`, `POST oauth/grants/{id}/revoke`, `GET oauth/consents`, `DELETE oauth/consents/{id}`), `src/aidoc/oauth/grants.py` (`revoke_grant(grant_id, reason)` publishes `mcp.oauth {action: "revoked"}`), `src/aidoc/oauth/clients.py` (`register_dcr`/CIMD first sight publish `{action: "registered"}`); tests `tests/oauth/test_admin_api.py`.

- [ ] **Step 1: Tests** — status: `enabled`, `mode`, `issuer`, `canonical_resource`, `prm_url`, `checks[]` with names `public_url_https`, `prm_reachable` (the server fetches its own PRM through `public_url` — in tests monkeypatch the fetch to hit the TestClient), `metadata_complete`, `allowed_hosts_has_public_host`, `forwarded_proto_seen` (false until a request with `X-Forwarded-Proto` arrived); clients list shows the CIMD client with `host`, `kind`, `grants_active`; `disable` → the grant's tokens 401 on `/mcp` and `/oauth/authorize` for that client is 400; grants list + revoke (409 on an already-revoked grant); consents list/delete; all routes 401 without the server token (`require_token` applies — they are under `/api`); SSE: the `mcp.oauth` events for `granted`, `revoked`, `disabled`, `registered` appear on `/api/events`.
- [ ] **Step 2–5:** RED → implement → GREEN (`uv run pytest tests/oauth tests/api/test_sse.py -q`) → commit `feat(api): /api/mcp/oauth status, clients, grants, consents; mcp.oauth events`.

---

### Task B11: Web — OAuth tab, Overview public-endpoint card, connector setup steps

**Files:** Modify `web/src/api/types.ts`, `web/src/api/mcp.ts` (queries/mutations for `/api/mcp/oauth/*`), `web/src/events/applyEvent.ts` (`mcp.oauth` → invalidate/merge clients, grants), `web/src/pages/McpPage.tsx` (fifth tab 「OAuth」, `?tab=oauth`; hidden when `status.oauth.enabled` is false but reachable with a short explainer), create `web/src/features/mcp/OAuthTab.tsx` (status card with the self-check list as green/red rows; Clients table (host, kind badge CIMD/DCR/手動, 最後出現, 有效授權數, 停用/啟用); Grants table (client host, scopes badges, 建立, 最後使用, 撤銷 with confirm dialog); 記住的瀏覽器 list with delete; 「連線設定」 for claude.ai (Settings → Connectors → Add custom connector → URL → 「Use Claude's published identity」) and ChatGPT (Settings → Security and login → Developer mode → URL) as numbered steps), `web/src/features/mcp/OverviewTab.tsx` (公開端點 card already from B2; add a 「OAuth 已啟用／未啟用」 line linking to the tab), `web/src/features/mcp/ConfigSnippets.tsx` (two new text-only tabs `claude.ai` / `ChatGPT` when OAuth is enabled); Vitest `OAuthTab.test.tsx`, `applyEvent.oauth.test.ts`; Playwright `web/e2e/oauth.spec.ts` (enable OAuth in the e2e server config with `public_url = "https://doc4ai.e2e"` — the e2e server is reached over http://127.0.0.1 but `TestClient`-style host spoofing is not available; so the e2e drives the **admin tab only**: seed a CIMD client + grant through a small test-only `POST /api/_e2e/oauth_seed` route registered when `AIDOC_TEST_ROUTES=1`, then asserts the tables render, revoke works, and the live `mcp.oauth` event updates the row).
- [ ] **Step 1–5:** RED (Vitest + e2e) → implement with the existing design system, 400 px usable → GREEN (`pnpm --dir web test && pnpm --dir web build && pnpm --dir web e2e`) → commit `feat(web): OAuth tab (clients, grants, consents, self-checks) and connector setup steps`.

---

### Task B12: Security suite (one test per spec §8 row) and parametrised regression

**Files:** Create `tests/oauth/test_security.py`; modify CI to run `tests/mcp` with `AIDOC_TEST_OAUTH=1` as a second job.

- [ ] **Step 1: Write the tests** — the names are fixed by spec §8; several already exist from B5–B8 and are **imported/re-asserted here by name** so the suite is the single checklist:

```python
"""Spec §8 security review — one test per row. Rows implemented earlier are referenced so this file is the checklist."""
import re
import time

import pytest

from tests.oauth.test_clients import test_cimd_blocks_private_and_redirect, test_dcr_register_and_limits  # noqa: F401  rows: CIMD SSRF, DCR flood
from tests.oauth.test_token_endpoint import test_code_reuse_revokes_grant, test_pat_is_not_an_oauth_credential  # noqa: F401
from tests.oauth.test_authorize import (test_bad_redirect_or_client_never_redirects, test_consent_bruteforce_429_and_logged,  # noqa: F401
                                        test_consent_csrf_rejected_and_headers, test_remember_cookie_skips_token_and_dies_with_admin_token)
from tests.oauth.test_verifier_chain import test_401_shape_without_oauth_is_unchanged, test_disable_kills_tokens, test_external_jwt_branch  # noqa: F401
from tests.oauth.test_token_endpoint import RES, client_row, pkce  # noqa: F401


def test_plain_pkce_rejected(oauth_client, client_row):
    r = oauth_client.get("/oauth/authorize", params=dict(response_type="code", client_id=client_row["client_id"], redirect_uri="https://claude.ai/api/mcp/auth_callback",
                                                        state="s", code_challenge="abc", code_challenge_method="plain", resource=RES), follow_redirects=False)
    assert r.status_code == 302 and "error=invalid_request" in r.headers["location"]
    assert oauth_client.get("/.well-known/oauth-authorization-server").json()["code_challenge_methods_supported"] == ["S256"]


def test_loopback_any_port(oauth_ctx, client_row):
    from aidoc.oauth.clients import redirect_allowed
    assert redirect_allowed(client_row, "http://localhost:60123/callback") and not redirect_allowed(client_row, "http://localhost:60123/evil")


def test_redirect_mismatch_does_not_redirect(oauth_client, client_row):
    test_bad_redirect_or_client_never_redirects(oauth_client, client_row)


def test_iss_in_response(oauth_client, client_row):
    from tests.oauth.test_authorize import AUTH, _consent_post
    v, c = pkce()
    html = oauth_client.get("/oauth/authorize", params={**AUTH, "code_challenge": c}).text
    assert "iss=https%3A%2F%2Fdoc4ai.test" in _consent_post(oauth_client, html).headers["location"]


def test_wrong_resource_invalid_target(oauth_client, oauth_ctx, client_row):
    from tests.oauth.test_token_endpoint import issue, token
    v, c = pkce()
    code = issue(oauth_ctx, client_row, c)
    r = token(oauth_client, grant_type="authorization_code", code=code, code_verifier=v, redirect_uri="https://claude.ai/api/mcp/auth_callback",
              client_id=client_row["client_id"], resource="https://doc4ai.test/other")
    assert r.json()["error"] == "invalid_target"


def test_jwt_wrong_aud_401(oauth_ctx, oauth_live, rsa_jwks):
    test_external_jwt_branch(oauth_ctx, oauth_live, rsa_jwks)


def test_at_from_other_resource_rejected(oauth_ctx, client_row):
    """A grant whose stored resource differs from the canonical URL (e.g. public_url changed) is refused."""
    from tests.oauth.test_verifier_chain import _access_token
    at, _ = _access_token(oauth_ctx, client_row)
    oauth_ctx.config.server.public_url = "https://moved.test"
    oauth_ctx.extras["mcp"].verifier.reload()
    assert oauth_ctx.extras["mcp"].verifier.verify(at).reason == "wrong_audience"


def test_refresh_rotation_and_replay(oauth_client, oauth_ctx, client_row):
    from tests.oauth.test_token_endpoint import test_code_exchange_issues_rotating_tokens
    test_code_exchange_issues_rotating_tokens(oauth_client, oauth_ctx, client_row)


def test_cimd_client_id_mismatch(fake_cimd, oauth_ctx):
    from tests.oauth.test_clients import test_cimd_fetch_rules
    test_cimd_fetch_rules(fake_cimd, oauth_ctx)


def test_consent_shows_host(oauth_client, client_row):
    from tests.oauth.test_authorize import test_consent_page_shows_host_and_scopes
    test_consent_page_shows_host_and_scopes(oauth_client, client_row)


def test_dcr_rate_limit(oauth_client, oauth_ctx):
    test_dcr_register_and_limits(oauth_client, oauth_ctx)


def test_consent_headers(oauth_client, client_row):
    from tests.oauth.test_authorize import AUTH
    v, c = pkce()
    r = oauth_client.get("/oauth/authorize", params={**AUTH, "code_challenge": c})
    assert r.headers["x-frame-options"] == "DENY" and "form-action 'self'" in r.headers["content-security-policy"] and r.headers["referrer-policy"] == "no-referrer"


def test_oauth_secrets_never_logged(oauth_client, oauth_ctx, client_row, caplog):
    from tests.oauth.test_token_endpoint import issue, token
    v, c = pkce()
    code = issue(oauth_ctx, client_row, c)
    with caplog.at_level("DEBUG"):
        body = token(oauth_client, grant_type="authorization_code", code=code, code_verifier=v, redirect_uri="https://claude.ai/api/mcp/auth_callback",
                     client_id=client_row["client_id"], resource=RES).json()
    blob = caplog.text + "".join(str(r) for r in oauth_ctx.store.list_mcp_calls(limit=50)) + "".join(str(e) for e in oauth_ctx.store.events_since(0))
    for secret in (code, v, body["access_token"], body["refresh_token"]):
        assert secret not in blob
    assert re.search(r"doc4ai_at_<redacted:[A-Za-z0-9]{4}>", blob) or "doc4ai_at_" not in blob


def test_forwarded_headers_only_from_trusted_proxy(ctx):
    from tests.unit.test_public_url import test_forwarded_headers_only_from_trusted_proxy as t
    t(ctx)


def test_jwt_clock_skew(oauth_ctx, oauth_live, rsa_jwks):
    test_external_jwt_branch(oauth_ctx, oauth_live, rsa_jwks)      # includes the 30 s-past-exp acceptance


def test_401_shape_with_prm(oauth_ctx, oauth_live, client_row):
    from tests.mcp.conftest import raw_post
    r = raw_post(oauth_live, "doc4ai_at_" + "x" * 50, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    www = r.headers["www-authenticate"]
    assert set(re.findall(r'(\w+)=', www)) == {"realm", "error", "error_description", "resource_metadata"}
    assert "exist" not in www.lower()


def test_consent_cookie_dies_with_admin_token(oauth_client, oauth_ctx, client_row):
    test_remember_cookie_skips_token_and_dies_with_admin_token(oauth_client, oauth_ctx, client_row)


@pytest.mark.parametrize("enabled", [False, True])
def test_pat_suite_runs_in_both_modes(enabled):
    """Documentary: CI runs `tests/mcp` with AIDOC_TEST_OAUTH=1 and without; see .github/workflows/tests.yml."""
    assert enabled in (False, True)
```

- [ ] **Step 2–5:** Run → fix anything RED → commit `test(oauth): security review suite (spec §8, one test per row)`. Update `.github/workflows/tests.yml` with the second `tests/mcp` job.

---

### Task B13: Docs — README connector section, `docs/deploy/https.md` finish, `[oauth]` in Settings page

**Files:** Modify `README.md` (MCP section: 「Cloud clients (claude.ai, Claude Desktop connectors, ChatGPT)」 — prerequisites: public HTTPS (`docs/deploy/https.md`), `[oauth] enabled = true`, connector steps; a sentence that PATs keep working), `docs/deploy/https.md` (finish with the OAuth self-check list and troubleshooting: 401 loop → PRM `resource` must equal the URL as entered; `invalid_target` → wrong `public_url`; Claude falls back to DCR → metadata missing `none`), `web/src/pages/SettingsPage.tsx` + `web/src/features/settings/*` (`[oauth]` section: enabled switch disabled with hint until `public_url` is https; TTL fields with ranges; `allow_dcr`), tests: README/doc presence test (`tests/unit/test_docs_oauth.py`), `SettingsPage.oauth.test.tsx`, `tests/api/test_system_settings.py` (append `[oauth]` ranges and the `public_url_required` 422).
- [ ] **Step 1–5:** RED → write → GREEN → commit `docs(oauth): connector setup, HTTPS guide completion, [oauth] settings section`.

---

### Task B14: Real-client acceptance (gated) and deviations

**Pre-condition:** gates G1, G2, G3 (for `remotes[]` only), G7 recorded in this file's deviations section.

Manual rows (record pass/fail/not-run, dates, client versions, the `client_id` observed):

1. **Public HTTPS**: `tailscale funnel --bg --https=443 3333` (or the chosen alternative); `aidoc.toml` `[server] public_url = "https://ulove-arrangement.tail74077f.ts.net"`; restart; `curl -I https://…/mcp` → 401 with `resource_metadata`; `GET /.well-known/oauth-protected-resource/mcp` → `resource` equals the public `/mcp` URL; OAuth tab self-checks all green.
2. **claude.ai custom connector** (spec §1.4 (5)): Settings → Connectors → Add custom connector → URL `https://…/mcp` → 「Use Claude's published identity」 → consent screen shows `claude.ai` host + scopes → allow → `search_library` in a chat → 呼叫紀錄 shows the OAuth client; revoke the grant in the OAuth tab → next tool call fails and claude.ai prompts to reconnect. Record the `client_id` the connector sent (spike S-B1) and whether Claude used CIMD or fell back to DCR (`oauth_client_meta.kind`).
3. **Claude Desktop** via the same connector (claude.ai-backed) — one `read_document`.
4. **Claude Code** (spec §1.4 (7)): `claude mcp add --transport http doc4ai-oauth https://…/mcp` (no header) → `/mcp` → browser consent → tools listed; `client_id` must be `https://claude.ai/oauth/claude-code-client-metadata`; redirect was `http://localhost:<port>/callback`.
5. **ChatGPT** (spec §1.4 (6)): developer mode → add MCP server → URL → sign in → consent → a `search_library` call; record CIMD (`https://chatgpt.com/oauth/client.json`) vs DCR and the redirect URI used. If developer mode is unavailable (gate G7): `npx @modelcontextprotocol/inspector` against the public URL with OAuth → full flow with PKCE visible in the inspector; record.
6. **Token lifecycle**: wait past `access_ttl_min` (set 5 min for the test) → the client refreshes transparently (呼叫紀錄 shows a new `token_id`); revoke → `invalid_grant` on the next refresh (server log).
7. **Compat API** (if present) with an OAuth access token in `Authorization: Bearer` → works (same chain).
8. Tear down or keep Funnel per gate G1; if torn down, set `oauth.enabled = false` and confirm `/mcp` 401 shape reverts to Phase 1.

Record everything in the deviations section; commit `docs(plan): OAuth acceptance results and deviations`.

---

## Phase acceptance (4b)

- [ ] `uv run pytest -m "not slow" -q` green (incl. `tests/oauth/`), `AIDOC_TEST_OAUTH=1 uv run pytest tests/mcp -q` green; `ruff check src tests` clean; `pnpm --dir web test`, `build`, `e2e` green.
- [ ] Spec §8 rows each have a passing test in `tests/oauth/test_security.py`.
- [ ] With `oauth.enabled = false` the PAT path is byte-identical to Phase 1 (401 shape test, `tests/mcp` unchanged).
- [ ] Metadata documents validate: PRM has `resource` == canonical; AS metadata advertises S256 only, `none` only, CIMD + RFC 9207 flags, no `offline_access`.
- [ ] CIMD fetcher refuses http, redirects, private/loopback/CGNAT addresses, oversize, mismatched `client_id`, symmetric auth methods; caches by `max-age`; rate-limited.
- [ ] Codes are single-use and 10-minute; refresh tokens rotate and replay revokes the grant; `resource` mismatch → `invalid_target`; `iss` present on every authorization response.
- [ ] Consent screen: host shown, scopes checkboxes with red warnings, CSRF nonce, same-origin check, brute-force 429, remember cookie tied to the server token, CSP/X-Frame-Options/Referrer-Policy headers.
- [ ] Admin: OAuth tab lists clients/grants/consents, revokes, shows self-checks; `mcp.oauth` events live.
- [ ] Redaction covers every new secret kind (test `test_oauth_secrets_never_logged`).
- [ ] Gates G1–G3, G7 recorded; acceptance rows 1–8 recorded with versions and the observed `client_id`s.
- [ ] Spike file `2026-10-oauth.md` complete; `docs/deploy/https.md` and README updated.

## Implementation notes / deviations

(Filled in while executing; gate answers first.)
