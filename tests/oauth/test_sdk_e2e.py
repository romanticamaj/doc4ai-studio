"""End to end with the real MCP Python SDK client (spec 2026-11-05 §1.3 (2)): 401 → PRM → AS metadata → DCR or CIMD →
authorize → consent → callback → token → tools/list, then refresh and revocation. The browser is a stand-in that GETs
the consent page and submits it like a same-origin form (no network beyond the live test server)."""
import asyncio
import re
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

import anyio
import httpx2
import pytest
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

from tests.oauth.conftest import CLAUDE_CODE_CIMD

CALLBACK = "http://127.0.0.1:50999/callback"
READ_TOOLS = {"search_library", "list_documents", "get_document_info", "read_document", "get_chunks", "get_job", "verify_document"}


class MemoryStorage:
    def __init__(self):
        self.tokens: OAuthToken | None = None
        self.client: OAuthClientInformationFull | None = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, info):
        self.client = info


class Browser:
    """Stands in for the user's browser: opens the authorization URL, checks it is the consent page, submits it."""

    def __init__(self, base: str, scopes=("doc4ai:read", "doc4ai:convert"), refuse_after: int | None = None):
        self.base, self.scopes, self.refuse_after = base, list(scopes), refuse_after
        self.opened: list[str] = []
        self.pages: list[str] = []
        self.queue: asyncio.Queue = asyncio.Queue()

    async def redirect_handler(self, url: str) -> None:
        self.opened.append(url)
        if self.refuse_after is not None and len(self.opened) > self.refuse_after:
            raise RuntimeError("the user closed the browser")
        assert url.startswith(self.base + "/oauth/authorize?"), url
        async with httpx2.AsyncClient(timeout=30) as c:
            page = await c.get(url)
            assert page.status_code == 200 and page.headers["content-type"].startswith("text/html"), page.text
            self.pages.append(page.text)
            blob = re.search(r'name="request" value="([^"]+)"', page.text).group(1)
            r = await c.post(self.base + "/oauth/authorize", data={"request": blob, "decision": "allow", "scopes": self.scopes},
                             headers={"Origin": self.base, "Sec-Fetch-Site": "same-origin"}, follow_redirects=False)
            assert r.status_code == 302, r.text
            loc = r.headers["location"]
            assert loc.startswith(CALLBACK + "?"), loc
            q = {k: v[0] for k, v in parse_qs(urlparse(loc).query).items()}
            await self.queue.put(AuthorizationCodeResult(code=q["code"], state=q.get("state"), iss=q.get("iss")))

    async def callback_handler(self) -> AuthorizationCodeResult:
        return await self.queue.get()


def _metadata():
    return OAuthClientMetadata(client_name="doc4ai-sdk-test", redirect_uris=[CALLBACK], grant_types=["authorization_code", "refresh_token"],
                               response_types=["code"], token_endpoint_auth_method="none")


@asynccontextmanager
async def session(base: str, provider):
    from mcp.client.client import Client
    from mcp.client.streamable_http import streamable_http_client
    from mcp.types import Implementation
    http = httpx2.AsyncClient(auth=provider, timeout=httpx2.Timeout(30, read=120))
    async with http, Client(streamable_http_client(f"{base}/mcp", http_client=http), mode="auto",
                            client_info=Implementation(name="doc4ai-sdk-oauth", version="1.0"), cache=None) as c:
        yield c


def _unwrap(e: BaseException) -> BaseException:
    while hasattr(e, "exceptions") and len(e.exceptions) == 1:
        e = e.exceptions[0]
    return e


def list_tools(base, provider) -> set[str]:
    async def go():
        async with session(base, provider) as s:
            return {t.name for t in (await s.list_tools()).tools}
    try:
        return anyio.run(go)
    except Exception as e:  # noqa: BLE001  the transport task group wraps whatever the body raised
        raise _unwrap(e) from None


def _provider(base, browser, storage, **kw):
    return OAuthClientProvider(server_url=f"{base}/mcp", client_metadata=_metadata(), storage=storage,
                               redirect_handler=browser.redirect_handler, callback_handler=browser.callback_handler, **kw)


def test_sdk_oauth_flow_dcr(live_oauth, oauth_ctx):
    browser, storage = Browser(live_oauth), MemoryStorage()
    assert list_tools(live_oauth, _provider(live_oauth, browser, storage)) >= READ_TOOLS | {"convert_document"}
    assert len(browser.opened) == 1
    q = parse_qs(urlparse(browser.opened[0]).query)
    assert q["code_challenge_method"] == ["S256"] and q["resource"] == [f"{live_oauth}/mcp"] and q["client_id"][0].startswith("doc4ai_cl_")
    assert storage.client.client_id == q["client_id"][0] and storage.tokens.access_token.startswith("doc4ai_at_")
    grants = oauth_ctx.store.list_oauth_grants()
    assert len(grants) == 1 and grants[0]["client_id"] == storage.client.client_id and grants[0]["resource"] == f"{live_oauth}/mcp"
    assert oauth_ctx.store.get_oauth_client(storage.client.client_id)["kind"] == "dcr"
    assert "本機註冊的 client" in browser.pages[0] and "doc4ai-sdk-test" in browser.pages[0]
    from fastapi.testclient import TestClient

    from aidoc.server.app import create_app
    with TestClient(create_app(oauth_ctx), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 1)) as c:
        rows = [t for t in c.get("/api/mcp/tokens").json()["tokens"] if t["kind"] == "oauth"]
    assert [r["name"] for r in rows] == ["doc4ai-sdk-test"] and rows[0]["status"] == "active"
    # a second session with the stored tokens: no new authorization
    assert list_tools(live_oauth, _provider(live_oauth, browser, storage)) >= READ_TOOLS
    assert len(browser.opened) == 1


def test_sdk_oauth_flow_cimd(live_oauth, oauth_ctx, fake_cimd):
    fake_cimd.put(CLAUDE_CODE_CIMD["client_id"], CLAUDE_CODE_CIMD, headers={"Cache-Control": "max-age=3600"})
    browser, storage = Browser(live_oauth, scopes=("doc4ai:read",)), MemoryStorage()
    provider = _provider(live_oauth, browser, storage, client_metadata_url=CLAUDE_CODE_CIMD["client_id"])
    assert list_tools(live_oauth, provider) == READ_TOOLS
    q = parse_qs(urlparse(browser.opened[0]).query)
    assert q["client_id"] == [CLAUDE_CODE_CIMD["client_id"]] and q["redirect_uri"] == [CALLBACK]      # matched portless 127.0.0.1
    assert "<strong>claude.ai</strong>" in browser.pages[0] and "Claude Code" in browser.pages[0]
    assert fake_cimd.calls_for(CLAUDE_CODE_CIMD["client_id"]) == 1
    g = oauth_ctx.store.list_oauth_grants()[0]
    assert g["client_id"] == CLAUDE_CODE_CIMD["client_id"] and g["scopes"] == ["doc4ai:read"]
    assert oauth_ctx.store.get_oauth_client(CLAUDE_CODE_CIMD["client_id"])["kind"] == "cimd"


def test_sdk_refresh_and_revoke(live_oauth, oauth_ctx):
    from aidoc.oauth.grants import GrantService
    oauth_ctx.config.oauth.access_ttl_min = 5
    browser, storage = Browser(live_oauth, refuse_after=1), MemoryStorage()
    provider = _provider(live_oauth, browser, storage)
    list_tools(live_oauth, provider)
    assert storage.tokens.expires_in == 300
    gid = oauth_ctx.store.list_oauth_grants()[0]["id"]
    first_at = storage.tokens.access_token
    # five minutes pass: the server's access token and the client's own clock both say expired
    oauth_ctx.store.con.execute("UPDATE oauth_tokens SET expires_at=? WHERE kind='at'", (time.time() - 1,))
    provider.context.token_expiry_time = time.time() - 1
    assert list_tools(live_oauth, provider) >= READ_TOOLS
    assert storage.tokens.access_token != first_at and len(browser.opened) == 1                        # refreshed, not re-authorized
    ats = oauth_ctx.store._qa("SELECT * FROM oauth_tokens WHERE kind='at' ORDER BY created_at")
    assert len(ats) == 2 and {a["grant_id"] for a in ats} == {gid}
    calls = [c for c in oauth_ctx.store.list_mcp_calls(limit=50) if c["method"] == "tools/list" and c["status"] == "ok"]
    assert len(calls) >= 2 and {c["token_id"] for c in calls} == {gid}                              # one identity across the refresh
    assert len(oauth_ctx.store.list_mcp_clients()) == 1
    # the admin revokes: the next call is refused, the provider asks the user once (who declines) and does not loop
    GrantService(oauth_ctx).revoke_grant(gid, "admin", time.time())
    with pytest.raises(Exception):  # noqa: B017  whatever the SDK raises for a declined re-authorization
        list_tools(live_oauth, provider)
    assert len(browser.opened) == 2
    assert len(oauth_ctx.store.list_oauth_grants()) == 1
    assert any(c["status"] == "auth_error" and c["error_code"] == "revoked" and c["token_id"] == gid
               for c in oauth_ctx.store.list_mcp_calls(limit=20))


def test_sdk_pkce_without_metadata(live_oauth, oauth_ctx, monkeypatch):
    """The MCP authorization spec says a client MUST refuse an AS whose metadata lacks code_challenge_methods_supported.
    Finding (plan deviations, Task 13): mcp 2.2.0 never reads that field; it always sends S256. This pins what the real
    client does, and that the flow is still PKCE-protected because this server only accepts S256 (whatever the
    metadata says): if the SDK ever starts refusing, this test fails and the finding is revisited."""
    from aidoc.oauth import metadata
    real = metadata.as_metadata

    def no_pkce(cfg, iss):
        m = real(cfg, iss)
        m.pop("code_challenge_methods_supported")
        return m
    monkeypatch.setattr(metadata, "as_metadata", no_pkce)
    browser = Browser(live_oauth)
    assert list_tools(live_oauth, _provider(live_oauth, browser, MemoryStorage())) >= READ_TOOLS
    q = parse_qs(urlparse(browser.opened[0]).query)
    assert q["code_challenge_method"] == ["S256"] and len(q["code_challenge"][0]) == 43
