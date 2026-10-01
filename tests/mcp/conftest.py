from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import anyio
import httpx
import pytest

from aidoc.mcp import tokens as T
from aidoc.mcp.tokens import load_or_create_secret

TINY_PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da6364f8cfc000"
                         "00030001008e4b1a5e0000000049454e44ae426082")


def page_md(n: int, heading: str | None = None, body: str | None = None) -> str:
    h = f"## {heading}\n\n" if heading else ""
    b = body or f"Page {n} text about topic {n}. " * 8
    return f"<!-- page: {n} -->\n{h}{b}\n"


@pytest.fixture
def mcp_env(ctx):
    """ctx (no server token, workers off) + a token issuer + the FastAPI app with /mcp mounted."""
    from aidoc.server.app import create_app
    secret = load_or_create_secret(ctx.config.data_dir)

    def issue(scopes=("doc4ai:read",), name="test token", expires_in=3600, rate=None):
        raw = T.generate_token()
        tid = ctx.store.create_api_token(name=name, prefix=T.display_prefix(raw), token_hash=T.token_hash(secret, raw),
                                         scopes=list(scopes), expires_at=None if expires_in is None else time.time() + expires_in,
                                         rate_limit_per_min=rate)
        return raw, tid
    app = create_app(ctx)
    return SimpleNamespace(ctx=ctx, issue=issue, app=app, runtime=ctx.extras["mcp"])


@pytest.fixture
def mcp_server(mcp_env, live_server):
    """Base URL of the live uvicorn server (lifespan on, so the SDK session manager runs)."""
    return live_server


@pytest.fixture
def make_doc(ctx):
    from aidoc.pageindex import index_document

    def _make(name="book", pages=3, md=None, status="ok", quality=None, engine="mineru", source_name=None,
              with_asset=True, sha=None):
        out = ctx.config.output_root() / name
        out.mkdir(parents=True, exist_ok=True)
        text = md if md is not None else "# Title\n\n" + "".join(page_md(n, heading=f"Chapter {n}") for n in range(1, pages + 1))
        (out / f"{name}.md").write_text(text, encoding="utf-8")
        q = quality or {"score": 0.98, "level": status if status in ("ok", "warn", "low") else "ok", "reasons": [], "pages": []}
        (out / f"{name}.json").write_text(json.dumps({"pages": pages or None, "quality": q}), encoding="utf-8")
        if with_asset:
            (out / "assets").mkdir(exist_ok=True)
            (out / "assets" / "p1_1.png").write_bytes(TINY_PNG)
        did = ctx.store.upsert_document(sha256=sha or ("s" * 60 + name[-4:].rjust(4, "0")), source_path=source_name or f"{name}.pdf",
                                        output_dir=str(out), engine=engine, quality=q, pages=pages or None, lang="cht",
                                        aidoc_version="0.1.0", status=status, created_at=time.time())
        doc = ctx.store.get_document(did)
        index_document(ctx.store, doc)
        return doc
    return _make


@asynccontextmanager
async def mcp_client(base_url: str, token: str | None, mode: str = "auto", client_info=None):
    """An SDK client against the live server. mode: "auto" (2026-07-28) or "legacy" (2025-11-25 initialize).
    Composition per spike S3: `Client(streamable_http_client(url, http_client=httpx2.AsyncClient(headers=...)))`."""
    import httpx2
    from mcp.client.client import Client
    from mcp.client.streamable_http import streamable_http_client
    from mcp.types import Implementation
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    http = httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30, read=120))
    info = client_info or Implementation(name="doc4ai-tests", version="1.0")
    try:
        async with http, Client(streamable_http_client(f"{base_url}/mcp", http_client=http), mode=mode,
                                client_info=info, cache=None) as c:
            yield c
    except Exception as e:  # noqa: BLE001  the transport task group wraps whatever the body raised
        raise _unwrap(e) from None


def _unwrap(e: BaseException) -> BaseException:
    while hasattr(e, "exceptions") and len(e.exceptions) == 1:
        e = e.exceptions[0]
    return e


def mcp_call(base_url, token, tool, arguments=None, mode="auto", client_info=None, progress=None):
    async def go():
        async with mcp_client(base_url, token, mode, client_info) as s:
            # like a real client: list first. Without it the SDK client fetches tools/list *after* the call (to
            # validate structuredContent), and the call would not be the newest mcp_calls row.
            await s.list_tools()
            return await s.call_tool(tool, arguments or {}, progress_callback=progress)
    return anyio.run(go)


def mcp_list_tools(base_url, token, mode="auto"):
    async def go():
        async with mcp_client(base_url, token, mode) as s:
            res = await s.list_tools()
            return res
    return anyio.run(go)


def raw_post(base_url, token, body: dict, *, modern=True, extra_headers=None):
    """A bare Streamable HTTP POST (no SDK) for header/status assertions."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if modern:
        headers.update({"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": body.get("method", "")})
        body = {**body, "params": {**body.get("params", {}), "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                                                       "io.modelcontextprotocol/clientCapabilities": {}}}}
        if body["method"] in ("tools/call", "resources/read"):
            headers["Mcp-Name"] = body["params"].get("name") or body["params"].get("uri", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(extra_headers or {})
    return httpx.post(f"{base_url}/mcp", json=body, headers=headers, timeout=30)
