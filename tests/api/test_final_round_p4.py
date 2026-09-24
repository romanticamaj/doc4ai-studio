"""Final fix round after the independent P4 checks (each test failed first)."""
import shutil

from tests.api.test_jobs_inputs import upload

XSS_HTML = """<!doctype html><html><head><title>Quarterly report</title>
<script>fetch("/api/queue/pause", {method: "POST"})</script></head><body>
<h1>Quarterly report with plenty of ordinary text for the quality gate</h1>
<p>This paragraph contains normal words so that the converted markdown is long enough to be judged ok.</p>
<p>See the <a href="assets/../source">original document</a> for the full tables.</p>
</body></html>"""


def _convert(client, ctx, src):
    job = client.post("/api/jobs", json={"inputs": [{"path": str(src)}]}).json()["job"]
    ctx.queue.process_next()
    return client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["document_id"]


def _hardened(r):
    assert r.headers.get("x-content-type-options") == "nosniff"
    csp = r.headers.get("content-security-policy", "")
    assert "sandbox" in csp and "default-src 'none'" in csp


# ---------------------------------------------------------------- C1 stored XSS through /source and /assets
def test_html_source_is_never_served_inline(client, ctx, tmp_root):
    src = tmp_root / "report.html"
    src.write_text(XSS_HTML, encoding="utf-8")
    doc_id = _convert(client, ctx, src)
    assert doc_id
    r = client.get(f"/api/documents/{doc_id}/source")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/octet-stream")
    assert r.headers["content-disposition"].startswith("attachment")
    _hardened(r)


def test_pdf_source_is_inline_but_hardened(client, ctx, fixtures):
    doc_id = _convert(client, ctx, fixtures / "text.pdf")
    r = client.get(f"/api/documents/{doc_id}/source")
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline")
    _hardened(r)


def test_assets_only_inline_for_safe_images(client, ctx, fixtures):
    import pathlib
    doc_id = _convert(client, ctx, fixtures / "text.pdf")
    assets = pathlib.Path(ctx.store.get_document(doc_id)["output_dir"]) / "assets"
    (assets / "evil.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                                     encoding="utf-8")
    (assets / "evil.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    png = next(p.name for p in assets.iterdir() if p.suffix == ".png")
    for name in ("evil.svg", "evil.html"):
        r = client.get(f"/api/documents/{doc_id}/assets/{name}")
        assert r.headers["content-type"].startswith("application/octet-stream"), name
        assert r.headers["content-disposition"].startswith("attachment"), name
        _hardened(r)
    r = client.get(f"/api/documents/{doc_id}/assets/{png}")
    assert r.headers["content-type"] == "image/png" and not r.headers.get("content-disposition", "").startswith(
        "attachment")
    _hardened(r)


def test_spa_pages_carry_a_csp(client, ctx, tmp_root):
    dist = tmp_root / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>spa</title>", encoding="utf-8")
    from fastapi.testclient import TestClient

    from aidoc.server.app import create_app
    with TestClient(create_app(ctx), base_url="http://127.0.0.1:8765") as c:
        r = c.get("/jobs")
    csp = r.headers.get("content-security-policy", "")
    for part in ("script-src 'self'", "object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'"):
        assert part in csp
    assert r.headers.get("x-content-type-options") == "nosniff"


# ---------------------------------------------------------------- M2 duplicate inputs are reported, not dropped
def test_identical_inputs_in_one_job_are_reported(client, ctx, fixtures, tmp_root):
    (tmp_root / "a").mkdir()
    (tmp_root / "b").mkdir()
    shutil.copy(fixtures / "text.pdf", tmp_root / "a" / "text.pdf")
    shutil.copy(fixtures / "text.pdf", tmp_root / "b" / "text.pdf")
    shutil.copy(fixtures / "sample.docx", tmp_root / "s.docx")
    r = client.post("/api/jobs", json={"inputs": [{"path": str(tmp_root / "a" / "text.pdf")},
                                                  {"path": str(tmp_root / "b" / "text.pdf")},
                                                  {"path": str(tmp_root / "s.docx")}]})
    job = r.json()["job"]
    assert job["progress"]["total"] == 3
    tasks = ctx.store.list_tasks(job["id"])
    dup = [t for t in tasks if t["status"] == "skipped"]
    assert len(dup) == 1 and "duplicate" in (dup[0]["error_msg"] or "")


# ---------------------------------------------------------------- M4 a no-op settings PUT writes nothing
def test_noop_settings_put_does_not_rewrite_the_file(client, ctx, tmp_root):
    toml = tmp_root / "aidoc.toml"
    toml.write_text('[general]\nlang = "cht"  # keep me\n', encoding="utf-8")
    from aidoc.config import load_config
    ctx.config = load_config()
    before = toml.read_text(encoding="utf-8")
    assert client.put("/api/settings", json={"general": {"lang": "cht"}}).status_code == 200
    assert toml.read_text(encoding="utf-8") == before
    assert client.put("/api/settings", json={"general": {"lang": "en"}}).status_code == 200
    after = toml.read_text(encoding="utf-8")
    assert 'lang = "en"' in after and "keep me" in after and "output_dir" not in after


# ---------------------------------------------------------------- M7 UNC paths in POST /jobs
def test_unc_paths_are_refused_in_jobs(client):
    r = client.post("/api/jobs", json={"inputs": [{"path": "\\\\10.255.255.1\\share\\a.pdf"}]})
    assert r.status_code == 422 and r.json()["error"] == "unc_path_forbidden"
    r = client.post("/api/jobs", json={"inputs": [{"path": "C:\\x.pdf"}], "output_dir": "//host/share/out"})
    assert r.status_code == 422 and r.json()["error"] == "unc_path_forbidden"


# ---------------------------------------------------------------- I2 disk full while writing a chunk
def test_disk_full_while_writing_a_chunk_is_507(client, ctx, fixtures, monkeypatch):
    import errno
    import hashlib
    data = (fixtures / "text.pdf").read_bytes()
    uid = client.post("/api/uploads", json={"filename": "t.pdf", "size": len(data),
                                            "sha256": hashlib.sha256(data).hexdigest()}).json()["upload_id"]
    import builtins
    real_open = builtins.open

    def full_open(path, mode="r", *a, **k):
        if str(path).endswith(".part") and "b" in mode:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_open(path, mode, *a, **k)
    monkeypatch.setattr("aidoc.server.uploads.open", full_open, raising=False)
    r = client.put(f"/api/uploads/{uid}?offset=0", content=data)
    assert r.status_code == 507 and r.json()["error"] == "disk_full"
    monkeypatch.undo()
    r2 = client.put(f"/api/uploads/{uid}?offset=0", content=data)   # space is back: the same upload continues
    assert r2.status_code == 200


def test_other_write_errors_are_500_with_a_code(client, fixtures, monkeypatch):
    import errno
    import hashlib
    data = (fixtures / "text.pdf").read_bytes()
    uid = client.post("/api/uploads", json={"filename": "t.pdf", "size": len(data),
                                            "sha256": hashlib.sha256(data).hexdigest()}).json()["upload_id"]
    import builtins
    real_open = builtins.open

    def bad_open(path, mode="r", *a, **k):
        if str(path).endswith(".part") and "b" in mode:
            raise OSError(errno.EIO, "I/O error")
        return real_open(path, mode, *a, **k)
    monkeypatch.setattr("aidoc.server.uploads.open", bad_open, raising=False)
    r = client.put(f"/api/uploads/{uid}?offset=0", content=data)
    assert r.status_code == 500 and r.json()["error"] == "upload_write_failed"


def test_upload_reuses_nothing_between_tests(client, fixtures):
    uid = upload(client, fixtures / "text.pdf")
    assert uid
