import io
import shutil
from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _own(pdf):
    with pymupdf.open(pdf) as d:
        return "".join(f"<!-- page: {i} -->\n\n{p.get_text().strip()}\n\n" for i, p in enumerate(d, 1))


def _converted(client, ctx, tmp_root, name="paged_furniture.pdf"):
    p = tmp_root / name
    shutil.copy(FIX / name, p)
    job = client.post("/api/jobs", json={"inputs": [{"path": str(p)}]}).json()["job"]
    ctx.queue.process_next()
    return client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["document_id"]


def test_multipart_verify(client):
    files = {"markdown": ("a.md", io.BytesIO(_own(FIX / "paged_furniture.pdf").encode()), "text/markdown"),
             "pdf": ("p.pdf", (FIX / "paged_furniture.pdf").read_bytes(), "application/pdf")}
    r = client.post("/api/verify", files=files, data={"format": "auto", "sample": "0"})
    body = r.json()
    assert r.status_code == 200 and body["summary"]["level"] == "ok" and body["workspace"] == "default"
    assert body["input"]["markdown"] == "a.md" and body["pdf"]["path"] == "p.pdf"          # no server temp paths
    assert body["page_map"]["alignment"]["sampled"] == 7


def test_multipart_same_as_cli_json(client, tmp_path):
    import json

    from aidoc.cli import main
    md = _own(FIX / "paged_furniture.pdf")
    (tmp_path / "a.md").write_text(md, encoding="utf-8")
    main(["verify", str(tmp_path / "a.md"), "--pdf", str(FIX / "paged_furniture.pdf"), "--json", str(tmp_path / "r.json")])
    cli = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    files = {"markdown": ("a.md", md.encode()), "pdf": ("p.pdf", (FIX / "paged_furniture.pdf").read_bytes())}
    api = client.post("/api/verify", files=files).json()
    for k in ("summary", "pages", "metrics", "verifiable"):
        assert api[k] == cli[k], k


def test_multipart_without_pdf_needs_pages(client):
    files = {"markdown": ("a.md", _own(FIX / "text.pdf").encode())}
    r = client.post("/api/verify", files=files, data={"pages": "3"})
    assert r.status_code == 200 and r.json()["verifiable"] == "partial"
    r = client.post("/api/verify", files=files)
    assert r.status_code == 422 and r.json()["error"] == "invalid_input"
    r = client.post("/api/verify", files=files, data={"pages": "3", "marker_regex": "(["})
    assert r.status_code == 422 and r.json()["error"] == "invalid_input"


def test_unverifiable_is_200(client):
    files = {"markdown": ("a.md", "沒有頁碼".encode()), "pdf": ("s.pdf", (FIX / "scanned_cht.pdf").read_bytes())}
    r = client.post("/api/verify", files=files)
    assert r.status_code == 200 and r.json()["verifiable"] is False


def test_doc_id_form_uses_library_document(client, ctx, tmp_root):
    doc_id = _converted(client, ctx, tmp_root)
    r = client.post("/api/verify", json={"doc_id": doc_id})
    body = r.json()
    assert r.status_code == 200, body
    assert body["previous"]["engine"] and body["summary"]["level"] in ("ok", "warn", "low") and body["pdf"]["pages"] == 8
    assert client.post("/api/verify", json={"doc_id": "nope"}).status_code == 404
    before = ctx.store.get_document(doc_id)
    client.post("/api/verify", json={"doc_id": doc_id})
    assert ctx.store.get_document(doc_id) == before                   # never writes the store


def test_local_path_form_is_guarded(client, ctx, tmp_path):
    md = tmp_path / "a.md"
    md.write_text(_own(FIX / "text.pdf"), encoding="utf-8")
    body = {"markdown_path": str(md), "pdf_path": str(FIX / "text.pdf")}
    from aidoc.server.app import create_app
    with TestClient(create_app(ctx), client=("10.0.0.7", 5555), base_url="http://127.0.0.1:8765") as remote:
        assert remote.post("/api/verify", json=body).status_code == 403      # non-loopback, no token
    r = client.post("/api/verify", json=body)
    assert r.status_code == 200 and r.json()["pdf"]["pages"] == 3
    r = client.post("/api/verify", json={"markdown_path": str(tmp_path / "missing.md"), "pdf_path": str(FIX / "text.pdf")})
    assert r.status_code == 422 and r.json()["error"] == "invalid_input"
    assert client.post("/api/verify", json={}).status_code == 422
    assert client.post("/api/verify", json={"doc_id": "x", "markdown_path": str(md)}).status_code == 422


def test_too_large(client):
    client.app.state.ctx.config.limits.upload_max_bytes = 10
    files = {"markdown": ("a.md", io.BytesIO(b"x" * 100), "text/markdown")}
    r = client.post("/api/verify", files=files, data={"pages": "1"})
    assert r.status_code == 413 and r.json()["error"] == "too_large"


def test_dangerous_marker_regex_is_422_never_500(client):
    files = {"markdown": ("a.md", b"[[p 1]]\nx"), "pdf": ("p.pdf", (FIX / "text.pdf").read_bytes())}
    for rx in (r"\[\[p (\d+)?\]\]", r"((a+)+\d)", "x" * 500):
        r = client.post("/api/verify", files=files, data={"marker_regex": rx})
        assert r.status_code == 422 and r.json()["error"] == "invalid_input", rx
