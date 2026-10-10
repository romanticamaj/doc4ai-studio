from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from aidoc.mcp import tokens as T
from aidoc.mcp.tokens import load_or_create_secret
from tests.mcp.conftest import TINY_PNG, make_doc, page_md  # noqa: F401  re-exported fixtures/helpers


@pytest.fixture
def compat_env(ctx, monkeypatch):
    """ctx (fake engines, workers off) + PAT issuer + TestClient against the app with /v1 mounted."""
    from fastapi.testclient import TestClient

    from aidoc.server.app import create_app
    monkeypatch.setenv("AIDOC_TEST_ROUTES", "1")          # registers GET /v1/_whoami
    secret = load_or_create_secret(ctx.config.data_dir)

    def issue(scopes=("doc4ai:read", "doc4ai:convert"), name="compat token", expires_in=3600):
        raw = T.generate_token()
        tid = ctx.store.create_api_token(name=name, prefix=T.display_prefix(raw), token_hash=T.token_hash(secret, raw),
                                         scopes=list(scopes), expires_at=time.time() + expires_in)
        return raw, tid
    app = create_app(ctx)
    with TestClient(app, client=("10.1.2.3", 40000), base_url="http://127.0.0.1:8765") as c:
        yield SimpleNamespace(ctx=ctx, issue=issue, client=c, app=app)


def last_call(ctx):
    rows = ctx.store.list_mcp_calls(limit=1)
    return rows[0] if rows else None
