"""docling-serve compatible API on the real engines (compat spec §9.3). Run:
AIDOC_REQUIRE_MANUAL=1 uv run pytest -m slow tests/integration/test_compat_real.py"""
from __future__ import annotations

import time

import pytest

from aidoc.mcp import tokens as T
from tests.manual import manual_sample, require_engine

pytestmark = pytest.mark.slow


@pytest.fixture
def real_compat(tmp_root):
    from fastapi.testclient import TestClient

    from aidoc.config import load_config
    from aidoc.mcp.tokens import load_or_create_secret
    from aidoc.server.app import build_context, create_app
    ctx = build_context(load_config(), token=None, start_workers=False)
    ctx.queue.start()
    secret = load_or_create_secret(ctx.config.data_dir)
    raw = T.generate_token()
    ctx.store.create_api_token(name="slow", prefix=T.display_prefix(raw), token_hash=T.token_hash(secret, raw),
                               scopes=["doc4ai:read", "doc4ai:convert"], expires_at=time.time() + 3600)
    with TestClient(create_app(ctx), client=("10.9.9.9", 1), base_url="http://127.0.0.1:8765") as c:
        yield c, raw, ctx
    ctx.queue.stop()
    ctx.close()


def _openwebui_post(client, raw, path, **params):
    with open(path, "rb") as f:                            # Open WebUI's DoclingLoader request (+ DOCLING_PARAMS)
        r = client.post("/v1/convert/file", files={"files": (path.name, f, "application/pdf")},
                        data={"image_export_mode": "placeholder", "md_page_break_placeholder": "\f", **params},
                        headers={"X-Api-Key": raw, "User-Agent": "python-requests/2.32.3"})
    assert r.status_code == 200, r.text
    return r.json()


def test_traditional_chinese_scan_goes_to_mineru_with_page_breaks(real_compat, fixtures):
    require_engine("mineru")
    client, raw, ctx = real_compat
    body = _openwebui_post(client, raw, fixtures / "scanned_cht.pdf")
    d = body["document"]
    assert body["status"] in ("success", "partial_success")
    assert d["doc4ai"]["engine"] == "mineru" and d["doc4ai"]["pages"] == 2
    assert d["md_content"].count("\f") == 1 and "<!-- page:" not in d["md_content"]
    assert all(seg.strip() for seg in d["md_content"].split("\f"))
    job = ctx.store.get_job(d["doc4ai"]["job_id"])
    assert job["origin"] == "compat"


def test_broken_font_pages_are_repaired_and_reported(real_compat):
    require_engine("docling")
    client, raw, _ = real_compat
    # DOCLING_PARAMS={"doc4ai_engine": "docling"}: the per-page repair path (auto routing may OCR the whole file)
    body = _openwebui_post(client, raw, manual_sample("hfad_garbled_mix.pdf"), doc4ai_engine="docling")
    d = body["document"]
    assert body["status"] == "success", body["errors"]
    warns = d["doc4ai"]["page_warnings"]
    assert warns and any(w["reason"] == "broken_text_layer" for w in warns) and all(w["repaired"] for w in warns)
    assert d["md_content"].count("\f") == d["doc4ai"]["pages"] - 1 == 8
