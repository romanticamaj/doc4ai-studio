"""Fixtures for the local MCP OAuth work (plan 2026-11-05 Task 0).

`oauth_client` talks to the app as `http://127.0.0.1:8765`, so the issuer the server derives from the Host header is
`ISSUER` and the canonical resource is `RES`. `token_oauth_*` is the same in token mode (server token set, bound to
0.0.0.0). `fake_cimd` replaces the network half of the CIMD fetcher (raw HTTP fetch + DNS) with an in-memory table;
every rule (status, redirect, size, content type, address checks, document validation) still runs for real."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from types import SimpleNamespace

import pytest

ISSUER = "http://127.0.0.1:8765"
RES = ISSUER + "/mcp"
SERVER_TOKEN = "admin-s3cret"
LOOPBACK_CB = "http://127.0.0.1/callback"
CB_DEFAULT = "http://127.0.0.1:3118/callback"           # the port a loopback client listens on in these tests
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def pkce() -> tuple[str, str]:
    v = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, c


def _make_ctx(tmp_root, monkeypatch, *, token, bind_host):
    from aidoc.config import load_config
    from aidoc.server.app import build_context
    from tests.fakes.scenario import fake_env, write_scenario
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    c = build_context(load_config(), token=token, start_workers=False)
    c.extras["bind_host"] = bind_host
    c.extras["bind_port"] = 8765
    return c


@pytest.fixture
def oauth_ctx(tmp_root, monkeypatch):
    c = _make_ctx(tmp_root, monkeypatch, token=None, bind_host="127.0.0.1")
    yield c
    c.close()


@pytest.fixture
def oauth_app(oauth_ctx):
    from aidoc.server.app import create_app
    return create_app(oauth_ctx)


@pytest.fixture
def oauth_client(oauth_app):
    from fastapi.testclient import TestClient
    with TestClient(oauth_app, client=("127.0.0.1", 50000), base_url=ISSUER) as c:
        yield c


@pytest.fixture
def token_oauth_ctx(tmp_root, monkeypatch):
    c = _make_ctx(tmp_root, monkeypatch, token=SERVER_TOKEN, bind_host="0.0.0.0")
    yield c
    c.close()


@pytest.fixture
def token_oauth_client(token_oauth_ctx):
    from fastapi.testclient import TestClient

    from aidoc.server.app import create_app
    with TestClient(create_app(token_oauth_ctx), client=("127.0.0.1", 50001), base_url=ISSUER) as c:
        yield c


# ---- CIMD network stand-in (replaces fetch_json only; the address rule inside it is the real check_addresses)

class FakeCimd:
    def __init__(self):
        self.table: dict[str, dict] = {}
        self.log: list[str] = []
        self._lock = threading.Lock()

    def put(self, url, doc, status=200, headers=None, addrs=None, content_type="application/json"):
        body = b"" if doc is None else (doc if isinstance(doc, bytes) else json.dumps(doc).encode("utf-8"))
        hdrs = {"content-type": content_type} if content_type else {}
        hdrs.update({k.lower(): v for k, v in (headers or {}).items()})
        self.table[url] = {"status": status, "headers": hdrs, "body": body, "addrs": addrs or ["104.18.1.1"]}

    @property
    def calls(self) -> int:
        return len(self.log)

    def calls_for(self, url) -> int:
        return sum(1 for u in self.log if u == url)

    # the two injectable module functions of aidoc.oauth.clients
    def fetch_json(self, url, *, max_bytes, timeout, ssl_context=None):
        """Like the real fetch_json: one lookup, every address checked by the real rule, then the "request"."""
        from aidoc.oauth.clients import FetchResult, check_addresses
        check_addresses(self.resolve_addrs(url.split("/")[2]))
        with self._lock:
            self.log.append(url)
        e = self.table.get(url)
        if e is None:
            raise OSError("connection refused")
        return FetchResult(status=e["status"], headers=dict(e["headers"]), body=e["body"][: max_bytes + 1])

    def resolve_addrs(self, host):
        for url, e in self.table.items():
            if url.split("/")[2].lower() == host.lower():
                return list(e["addrs"])
        return ["104.18.1.1"]


@pytest.fixture
def fake_cimd(monkeypatch):
    from aidoc.oauth import clients
    f = FakeCimd()
    monkeypatch.setattr(clients, "fetch_json", f.fetch_json)
    return f


# Claude Code's CIMD document as published at https://claude.ai/oauth/claude-code-client-metadata (2026-11-05)
CLAUDE_CODE_CIMD = {"client_id": "https://claude.ai/oauth/claude-code-client-metadata", "client_name": "Claude Code",
                    "client_uri": "https://claude.ai",
                    "redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"],
                    "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                    "token_endpoint_auth_method": "none"}


# ---- DCR and authorize helpers

def register_client(client, redirect_uris=(LOOPBACK_CB,), name="Test IDE", **extra) -> dict:
    r = client.post("/oauth/register", json={"client_name": name, "redirect_uris": list(redirect_uris),
                                             "token_endpoint_auth_method": "none", **extra})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def dcr_client(oauth_client) -> dict:
    return register_client(oauth_client)


def auth_params(row: dict, challenge: str | None = None, **over) -> dict:
    if challenge is None:
        challenge = pkce()[1]
    p = {"response_type": "code", "client_id": row["client_id"], "redirect_uri": "http://127.0.0.1:3118/callback",
         "scope": "doc4ai:read doc4ai:convert", "state": "st4te", "code_challenge": challenge,
         "code_challenge_method": "S256", "resource": RES}
    for k, v in over.items():
        if v is None:
            p.pop(k, None)
        else:
            p[k] = v
    return p


_REQ_RE = re.compile(r'name="request" value="([^"]+)"')


def request_blob(html: str) -> str:
    m = _REQ_RE.search(html)
    assert m, "no signed request field in the consent page"
    return m.group(1)


def consent_post(client, html, headers=None, **over):
    form = {"request": request_blob(html), "decision": "allow", "scopes": ["doc4ai:read", "doc4ai:convert"], **over}
    hdrs = {"Origin": ISSUER, "Sec-Fetch-Site": "same-origin"} if headers is None else headers
    return client.post("/oauth/authorize", data=form, headers=hdrs, follow_redirects=False)


def location_query(r) -> dict:
    from urllib.parse import parse_qs, urlparse
    return {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}


def authorize_code(client, row, *, scopes=("doc4ai:read", "doc4ai:convert"), redirect="http://127.0.0.1:3118/callback",
                   requested="doc4ai:read doc4ai:convert", resource=RES):
    """GET the consent page and allow: returns (code, verifier)."""
    v, c = pkce()
    html = client.get("/oauth/authorize", params=auth_params(row, c, redirect_uri=redirect, scope=requested,
                                                              resource=resource)).text
    r = consent_post(client, html, scopes=list(scopes))
    assert r.status_code == 302, r.text
    return location_query(r)["code"], v


def exchange(client, row, code, verifier, redirect="http://127.0.0.1:3118/callback", **extra):
    form = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier, "redirect_uri": redirect,
            "client_id": row["client_id"], **extra}
    return client.post("/oauth/token", data=form)


def mint_tokens(ctx, *, scopes=("doc4ai:read",), resource=RES, client_id=None, name="Test IDE",
                redirect="http://127.0.0.1:3118/callback") -> SimpleNamespace:
    """A grant straight through GrantService (no HTTP): returns access_token, refresh_token, grant_id, client_id."""
    from aidoc.oauth.grants import GrantService
    from aidoc.oauth.tokens import generate_client_id
    cid = client_id or generate_client_id()
    if ctx.store.get_oauth_client(cid) is None:
        ctx.store.upsert_oauth_client(client_id=cid, kind="dcr", name=name, redirect_uris=[LOOPBACK_CB])
    row = ctx.store.get_oauth_client(cid)
    svc = GrantService(ctx)
    v, c = pkce()
    now = time.time()
    code = svc.issue_code(row, redirect, c, list(scopes), resource, "127.0.0.1", now)
    tr = svc.exchange_code({"grant_type": "authorization_code", "code": code, "code_verifier": v, "redirect_uri": redirect,
                            "client_id": cid}, "127.0.0.1", now, resource=resource)
    grant = ctx.store.get_oauth_token_by_hash(svc.hash(tr.access_token))["grant_id"]
    return SimpleNamespace(access_token=tr.access_token, refresh_token=tr.refresh_token, grant_id=grant, client_id=cid)


@pytest.fixture
def live_oauth(oauth_ctx):
    """A real uvicorn server over oauth_ctx (ephemeral port; lifespan on so the SDK session manager runs)."""
    import time as _time

    import uvicorn

    from aidoc.server.app import create_app
    server = uvicorn.Server(uvicorn.Config(create_app(oauth_ctx), host="127.0.0.1", port=0, log_level="warning",
                                           lifespan="on"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    deadline = _time.time() + 10
    while not server.started:
        if _time.time() > deadline:
            raise RuntimeError("uvicorn did not start")
        _time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    oauth_ctx.extras["bind_port"] = port              # like `aidoc serve`: the issuer's port is the bound one
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    th.join(10)
