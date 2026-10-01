"""Transfer over a slow, high-latency link (Tailscale DERP, ~1 s RTT): text is gzip-compressed, built files are
cached for as long as their names say they can be, and nothing that must stream or is already binary is touched."""
import gzip
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

from aidoc.server.app import create_app
from aidoc.server.transfer import CompressionMiddleware
from tests.api.test_documents import converted

GZ = {"Accept-Encoding": "gzip"}
IDENTITY = {"Accept-Encoding": "identity"}
BIG = "lorem ipsum dolor sit amet " * 200          # 5.4 KB, very compressible


def raw_get(client, url, headers):
    """The bytes as sent (httpx would transparently decode gzip)."""
    with client.stream("GET", url, headers=headers) as r:
        body = b"".join(r.iter_raw())
    return r, body


# ------------------------------------------------------------------------------------------ middleware unit level
def mini_app(*routes):
    return CompressionMiddleware(Starlette(routes=list(routes)))


def test_compresses_text_above_minimum_and_keeps_validators_weak():
    async def big(request):
        return PlainTextResponse(BIG, headers={"ETag": '"abc"', "Cache-Control": "no-cache"})
    with TestClient(mini_app(Route("/big", big))) as c:
        r, body = raw_get(c, "/big", GZ)
        assert r.headers["content-encoding"] == "gzip"
        assert gzip.decompress(body).decode() == BIG
        assert int(r.headers["content-length"]) == len(body) < len(BIG) / 5
        assert "accept-encoding" in r.headers["vary"].lower()
        assert r.headers["etag"] == 'W/"abc"' and r.headers["cache-control"] == "no-cache"
        r, body = raw_get(c, "/big", IDENTITY)
        assert "content-encoding" not in r.headers and body.decode() == BIG and r.headers["etag"] == '"abc"'
        assert "accept-encoding" in r.headers["vary"].lower()


def test_small_bodies_are_left_alone():
    async def small(request):
        return PlainTextResponse("x" * 500)
    with TestClient(mini_app(Route("/s", small))) as c:
        r, body = raw_get(c, "/s", GZ)
        assert "content-encoding" not in r.headers and body == b"x" * 500


def test_streamed_bodies_are_compressed_incrementally():
    async def gen():
        for _ in range(50):
            yield BIG.encode()

    async def stream(request):
        return StreamingResponse(gen(), media_type="text/javascript")
    with TestClient(mini_app(Route("/s", stream))) as c:
        r, body = raw_get(c, "/s", GZ)
        assert r.headers["content-encoding"] == "gzip" and gzip.decompress(body) == BIG.encode() * 50


@pytest.mark.parametrize("path,kwargs", [
    ("/mcp", {"media_type": "text/plain"}),                       # future MCP mount (Streamable HTTP, SSE)
    ("/mcp/x", {"media_type": "application/json"}),
    ("/api/events", {"media_type": "text/plain"}),                # SSE: compression would hold events back
    ("/sse", {"media_type": "text/event-stream"}),
    ("/pdf", {"media_type": "application/pdf"}),                  # binary: nothing to gain
    ("/png", {"media_type": "image/png"}),
    ("/zip", {"media_type": "application/zip"}),
    ("/bin", {"media_type": "application/octet-stream"}),
    ("/enc", {"media_type": "text/plain", "headers": {"Content-Encoding": "br"}}),   # already encoded
    ("/part", {"media_type": "text/plain", "status_code": 206}),  # a byte range must stay a byte range
])
def test_never_compressed(path, kwargs):
    async def h(request):
        return Response(BIG, **kwargs)
    with TestClient(mini_app(Route(path, h))) as c:
        r, body = raw_get(c, path, GZ)
        assert r.headers.get("content-encoding") in (None, kwargs.get("headers", {}).get("Content-Encoding"))
        assert body == BIG.encode()


def test_head_is_left_alone():
    async def h(request):
        return PlainTextResponse(BIG)
    with TestClient(mini_app(Route("/h", h, methods=["GET", "HEAD"]))) as c:
        r = c.head("/h", headers=GZ)
        assert "content-encoding" not in r.headers


# ------------------------------------------------------------------------------------------ the real app
def test_markdown_is_gzipped_and_revalidates_with_the_weak_tag(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures)
    url = f"/api/documents/{did}/markdown"
    plain = client.get(url, headers=IDENTITY)
    if len(plain.content) < 1024:
        pytest.skip("fixture markdown below the compression minimum")
    r, body = raw_get(client, url, GZ)
    assert r.headers["content-encoding"] == "gzip" and gzip.decompress(body) == plain.content
    assert r.headers["cache-control"] == "no-cache" and r.headers["etag"].startswith("W/")
    assert client.get(url, headers={**GZ, "If-None-Match": r.headers["etag"]}).status_code == 304
    assert client.get(url, headers={**IDENTITY, "If-None-Match": plain.headers["etag"]}).status_code == 304


def test_json_is_gzipped_with_the_envelope_intact(client, ctx, tmp_root, fixtures):
    converted(client, ctx, tmp_root, fixtures)          # one document row is already well over 1 KB of JSON
    r, body = raw_get(client, "/api/documents", GZ)
    assert r.headers["content-encoding"] == "gzip"
    data = json.loads(gzip.decompress(body))
    assert data["workspace"] == "default" and len(data["documents"]) == 1


def test_source_pdf_and_its_ranges_are_never_gzipped(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures)
    url = f"/api/documents/{did}/source"
    r, body = raw_get(client, url, GZ)
    assert r.status_code == 200 and "content-encoding" not in r.headers and body.startswith(b"%PDF")
    r, body = raw_get(client, url, {**GZ, "Range": "bytes=0-2047"})
    assert r.status_code == 206 and "content-encoding" not in r.headers and len(body) == 2048


def test_sse_stream_is_not_gzipped(live_server):
    with httpx.Client(base_url=live_server, timeout=5) as c, \
            c.stream("GET", "/api/events", headers=GZ) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        assert "content-encoding" not in r.headers


# ------------------------------------------------------------------------------------------ built Web UI caching
IMMUTABLE = "public, max-age=31536000, immutable"


@pytest.fixture
def dist_client(ctx, tmp_root):
    dist = tmp_root / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "pdfjs" / "6.3.289" / "cmaps").mkdir(parents=True)
    (dist / "pdfjs" / "cmaps").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>spa</title>" + "<!-- pad -->" * 200, encoding="utf-8")
    (dist / "theme-init.js").write_text("document.documentElement.dataset.x=1;", encoding="utf-8")
    (dist / "assets" / "index-CE0ppiH5.js").write_text("console.log(1);" * 300, encoding="utf-8")
    (dist / "assets" / "font-BgDaEnEv.woff2").write_bytes(b"wOF2" + bytes(3000))
    (dist / "pdfjs" / "6.3.289" / "cmaps" / "UniGB-UCS2-H.bcmap").write_bytes(b"\xe0" + bytes(2000))
    (dist / "pdfjs" / "cmaps" / "UniGB-UCS2-H.bcmap").write_bytes(b"\xe0" + bytes(2000))
    with TestClient(create_app(ctx), client=("127.0.0.1", 50000), base_url="http://127.0.0.1:8765") as c:
        yield c


def test_hashed_and_versioned_files_are_cached_for_a_year(dist_client):
    for url in ("/assets/index-CE0ppiH5.js", "/assets/font-BgDaEnEv.woff2", "/pdfjs/6.3.289/cmaps/UniGB-UCS2-H.bcmap"):
        r = dist_client.get(url)
        assert r.status_code == 200 and r.headers["cache-control"] == IMMUTABLE, url


@pytest.mark.parametrize("url,policy", [
    ("/", "no-cache"), ("/index.html", "no-cache"), ("/documents/abc", "no-cache"),   # a new build shows at once
    # small unhashed files: used at once from cache (theme-init.js blocks rendering: a revalidation would cost a
    # full round trip before the page can render), revalidated in the background so the next load has a new build
    ("/theme-init.js", "public, max-age=0, stale-while-revalidate=604800"),
    ("/pdfjs/cmaps/UniGB-UCS2-H.bcmap", "public, max-age=0, stale-while-revalidate=604800"),
])
def test_unversioned_files_revalidate(dist_client, url, policy):
    r = dist_client.get(url)
    assert r.status_code == 200 and r.headers["cache-control"] == policy and r.headers.get("etag"), url
    again = dist_client.get(url, headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304 and again.headers["etag"].removeprefix("W/") == r.headers["etag"].removeprefix("W/")


def test_built_js_and_html_are_gzipped_fonts_are_not(dist_client):
    r, body = raw_get(dist_client, "/assets/index-CE0ppiH5.js", GZ)
    assert r.headers["content-encoding"] == "gzip" and gzip.decompress(body) == b"console.log(1);" * 300
    r, _ = raw_get(dist_client, "/documents/abc", GZ)
    assert r.headers["content-encoding"] == "gzip" and r.headers["content-security-policy"]
    r, _ = raw_get(dist_client, "/assets/font-BgDaEnEv.woff2", GZ)
    assert "content-encoding" not in r.headers


# ------------------------------------------------------------------------------------------ PDF open bundle
def parse_ranges(header):
    return [tuple(int(x) for x in part.split("-")) for part in header.split(",") if part]


def test_pdf_open_bundle_is_the_listed_byte_ranges(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures, name="big.pdf")
    src = (tmp_root / "big.pdf").read_bytes()
    url = f"/api/documents/{did}/source/open?chunk=1024"
    r = client.get(url, headers=IDENTITY)
    assert r.status_code == 200 and r.headers["content-type"] == "application/vnd.aidoc.pdf-ranges"
    assert int(r.headers["x-pdf-size"]) == len(src)
    ranges = parse_ranges(r.headers["x-pdf-ranges"])
    assert ranges[0][0] == 0 and ranges[-1][1] == len(src)
    assert r.content == b"".join(src[b:e] for b, e in ranges)
    assert r.headers["cache-control"] == "no-cache" and r.headers["x-content-type-options"] == "nosniff"
    assert client.get(url, headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    z, body = raw_get(client, url, GZ)
    assert z.headers["content-encoding"] == "gzip" and gzip.decompress(body) == r.content
    assert client.get(url, headers={"If-None-Match": z.headers["etag"]}).status_code == 304
    other = client.get(f"/api/documents/{did}/source/open?chunk=2048", headers={"If-None-Match": r.headers["etag"]})
    assert other.status_code == 200                                    # the chunk size is part of the tag


def test_pdf_open_bundle_refuses_bad_chunks_and_non_pdfs(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures, name="big.pdf")
    for bad in ("0", "100", str(64 * 1024 * 1024), "x"):
        assert client.get(f"/api/documents/{did}/source/open?chunk={bad}").status_code in (400, 422), bad
    docx = converted(client, ctx, tmp_root, fixtures, name="sample.docx")
    r = client.get(f"/api/documents/{docx}/source/open?chunk=65536")
    assert r.status_code == 415 and r.json()["error"] == "not_pdf"
