"""Office preview over the API (LibreOffice → PDF), with a fake soffice script."""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import pytest

from aidoc import preview as P

FAKE = Path(__file__).resolve().parents[1] / "fakes" / "fake_soffice.py"


def converted(client, ctx, tmp_root, fixtures, name):
    p = tmp_root / name
    shutil.copy(fixtures / name, p)
    job = client.post("/api/jobs", json={"inputs": [{"path": str(p)}]}).json()["job"]
    ctx.queue.process_next()
    return client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["document_id"]


@pytest.fixture
def fake_preview(ctx, tmp_root):
    def make(mode="ok"):
        old = ctx.extras.get("preview")
        if old is not None:
            old.stop()
        m = P.PreviewManager(ctx.config.data_dir, lambda: ctx.config.preview, exe=[sys.executable, str(FAKE)],
                             env={"FAKE_SOFFICE_MODE": mode, "FAKE_SOFFICE_LOG": str(tmp_root / "soffice_calls.jsonl")},
                             publish=lambda payload: ctx.bus.publish("document.preview", payload["document_id"],
                                                                     payload))
        ctx.extras["preview"] = m
        return m
    return make


def _wait_status(client, did, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = client.get(f"/api/documents/{did}").json()["preview"]
        if p["status"] in ("ready", "failed"):
            return p
        time.sleep(0.05)
    raise AssertionError("preview did not finish")


def test_pdf_documents_have_no_preview_field_value(client, ctx, tmp_root, fixtures, fake_preview):
    fake_preview()
    did = converted(client, ctx, tmp_root, fixtures, "text.pdf")
    assert client.get(f"/api/documents/{did}").json()["preview"] is None
    r = client.post(f"/api/documents/{did}/preview")
    assert r.status_code == 409 and r.json()["error"] == "not_office"


def test_office_preview_lifecycle(client, ctx, tmp_root, fixtures, fake_preview):
    fake_preview()
    did = converted(client, ctx, tmp_root, fixtures, "sample.docx")
    d = client.get(f"/api/documents/{did}").json()
    assert d["preview"] == {"status": "none"}
    r = client.get(f"/api/documents/{did}/preview")
    assert r.status_code == 409 and r.json()["error"] == "preview_not_ready" and r.json()["preview"]["status"] == "none"
    r = client.post(f"/api/documents/{did}/preview")
    assert r.status_code == 202 and r.json()["preview"] == {"status": "pending"}
    assert _wait_status(client, did) == {"status": "ready", "pages": 2}
    ev = [e for e in ctx.store.events_since(0) if e["kind"] == "document.preview"]
    assert ev and ev[-1]["payload"] == {"document_id": did, "status": "ready", "pages": 2}
    assert ev[-1]["ref_id"] == did
    # served like a PDF source: inline, nosniff, sandbox CSP, revalidated with an ETag, byte ranges
    r = client.get(f"/api/documents/{did}/preview")
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert r.headers["cache-control"] == "no-cache" and r.headers["etag"]
    assert client.get(f"/api/documents/{did}/preview", headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    part = client.get(f"/api/documents/{did}/preview", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and part.content == r.content[:100]
    # the open bundle (pdf.js first-pages prefetch) works for previews too
    b = client.get(f"/api/documents/{did}/preview/open?chunk=65536")
    assert b.status_code == 200 and int(b.headers["x-pdf-size"]) == len(r.content) and b.headers["x-pdf-ranges"]
    # the original stays downloadable and is untouched
    src = client.get(f"/api/documents/{did}/source")
    assert src.status_code == 200 and src.content == (fixtures / "sample.docx").read_bytes()
    assert src.headers["content-type"] == "application/octet-stream"
    # second POST is a cache hit
    assert client.post(f"/api/documents/{did}/preview").json()["preview"]["status"] == "ready"
    assert len((tmp_root / "soffice_calls.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_preview_failure_and_retry(client, ctx, tmp_root, fixtures, fake_preview):
    fake_preview("fail")
    did = converted(client, ctx, tmp_root, fixtures, "sample.pptx")
    client.post(f"/api/documents/{did}/preview")
    p = _wait_status(client, did)
    assert p == {"status": "failed", "reason": "conversion_failed"}
    assert client.post(f"/api/documents/{did}/preview").json()["preview"]["status"] == "failed"
    fake_preview("ok")
    r = client.post(f"/api/documents/{did}/preview", json={"retry": True})
    assert r.status_code == 202
    assert _wait_status(client, did)["status"] == "ready"


def test_soffice_missing_is_reported(client, ctx, tmp_root, fixtures):
    old = ctx.extras.get("preview")
    if old is not None:
        old.stop()
    ctx.extras["preview"] = P.PreviewManager(ctx.config.data_dir, lambda: ctx.config.preview, locate=lambda: None)
    did = converted(client, ctx, tmp_root, fixtures, "sample.xlsx")
    assert client.get(f"/api/documents/{did}").json()["preview"] == {"status": "failed", "reason": "soffice_missing"}
    r = client.post(f"/api/documents/{did}/preview")
    assert r.status_code == 200 and r.json()["preview"]["reason"] == "soffice_missing"


def test_preview_of_unknown_document(client):
    assert client.get("/api/documents/nope/preview").status_code == 404
    assert client.post("/api/documents/nope/preview").status_code == 404


def test_system_reports_libreoffice(client, ctx, tmp_root, monkeypatch):
    exe = tmp_root / "LO" / "program" / "soffice.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    (exe.parent / "version.ini").write_text("[Version]\nMsiProductVersion=25.8.1.1\n", encoding="utf-8")
    ctx.config.preview.soffice_path = str(exe)
    lo = client.get("/api/system").json()["libreoffice"]
    assert lo == {"found": True, "path": str(exe), "version": "25.8.1.1", "configured": True}
    ctx.config.preview.soffice_path = str(tmp_root / "nope.exe")
    assert client.get("/api/system").json()["libreoffice"]["found"] is False


def test_settings_preview_section(client, ctx):
    s = client.get("/api/settings").json()["settings"]["preview"]
    assert s == {"enabled": True, "soffice_path": "", "timeout_s": 180, "max_mb": 200}
    r = client.put("/api/settings", json={"preview": {"timeout_s": 60, "max_mb": 50, "enabled": False}})
    assert r.status_code == 200 and r.json()["settings"]["preview"]["timeout_s"] == 60
    assert ctx.config.preview.enabled is False
    # the executable path is never settable over the API (it would let a token holder run any program)
    r = client.put("/api/settings", json={"preview": {"soffice_path": "C:/Windows/System32/cmd.exe"}})
    assert r.status_code == 403 and r.json()["error"] == "preview_path_readonly"
    assert client.put("/api/settings", json={"preview": {"soffice_path": ""}}).status_code == 200   # unchanged: ok
    for bad in ({"timeout_s": 0}, {"timeout_s": 100000}, {"max_mb": 0}, {"max_mb": 5000}):
        r = client.put("/api/settings", json={"preview": bad})
        assert r.status_code == 422, bad


def test_maintenance_purges_previews_of_deleted_documents(client, ctx, tmp_root, fixtures, fake_preview):
    from aidoc.server.maintenance import Maintenance
    fake_preview()
    did = converted(client, ctx, tmp_root, fixtures, "sample.docx")
    client.post(f"/api/documents/{did}/preview")
    _wait_status(client, did)
    sha = ctx.store.get_document(did)["sha256"]
    pdf = ctx.config.data_dir / "previews" / f"{sha}.pdf"
    assert pdf.is_file()
    assert Maintenance(ctx).run_once()["previews_purged"] == 0 and pdf.is_file()
    ctx.store.set_document_status(did, "orphaned")
    ctx.store.delete_documents("orphaned")
    assert Maintenance(ctx).run_once()["previews_purged"] == 1 and not pdf.exists()
