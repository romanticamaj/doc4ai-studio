"""GET /sources, POST /sources/backfill, POST /documents/{id}/source-copy (spec 2026-10-29 §10.1; plan Task 7)."""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

from doc4ai import source_copy
from tests.api.test_documents import converted


def _wait(ctx, kind, pred, timeout=10.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(e["kind"] == kind and pred(e["payload"]) for e in ctx.store.events_since(0)):
            return
        time.sleep(0.05)
    raise AssertionError(f"no {kind} event")


def _pre_v7(ctx, did):
    d = ctx.store.get_document(did)
    if d["source_copy_path"]:
        os.unlink(d["source_copy_path"])
    ctx.store.set_source_copy(did, path=None, status=None, error=None)


def test_summary_counts(client, ctx, tmp_root, fixtures):
    a = converted(client, ctx, tmp_root, fixtures)
    b = converted(client, ctx, tmp_root, fixtures, name="sample.docx")
    s = client.get("/api/sources").json()
    assert (s["documents"], s["copied"], s["not_copied"]) == (2, 2, 0)
    assert s["source_dir"] == str(ctx.config.source_root()) and s["bytes"] > 0 and s["free_bytes"] > 0
    _pre_v7(ctx, b)
    s = client.get("/api/sources").json()
    assert (s["copied"], s["not_copied"], s["file_missing"]) == (1, 1, 0)
    assert s["orphaned_copies"] == {"count": 0, "bytes": 0, "shared": 0}
    assert a


def test_backfill_dry_and_real_match_the_unit_report(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures)
    _pre_v7(ctx, did)
    dry = client.post("/api/sources/backfill", json={"dry_run": True}).json()
    assert dry["dry_run"] is True and dry["totals"]["copied"] == 1
    assert ctx.store.get_document(did)["source_copy_status"] is None              # dry: nothing written
    real = client.post("/api/sources/backfill", json={}).json()
    assert real["totals"]["copied"] == 1 and real["documents"][0]["from"] in ("original", "work_copy")
    assert [(d["id"], d["outcome"]) for d in dry["documents"]] == [(d["id"], d["outcome"]) for d in real["documents"]]
    again = source_copy.backfill(ctx.store, ctx.config, dry_run=True)
    assert again["totals"]["already_present"] == 1
    _wait(ctx, "sources.backfill", lambda p: p.get("finished") and not p["dry_run"])
    _wait(ctx, "sources.backfill", lambda p: p.get("done") == 1)
    _wait(ctx, "document.source_copy", lambda p: p["document_id"] == did and p["status"] == "copied")


def test_backfill_409_while_running(client, ctx, tmp_root, fixtures, monkeypatch):
    converted(client, ctx, tmp_root, fixtures)
    entered, release = threading.Event(), threading.Event()
    real = source_copy.ensure_copy

    def slow(*a, **k):
        entered.set()
        release.wait(10)
        return real(*a, **k)
    monkeypatch.setattr(source_copy, "ensure_copy", slow)
    th = threading.Thread(target=lambda: source_copy.backfill(ctx.store, ctx.config, dry_run=True))
    th.start()
    try:
        assert entered.wait(10)
        r = client.post("/api/sources/backfill", json={"dry_run": True})
        assert r.status_code == 409 and r.json()["error"] == "backfill_running"
    finally:
        release.set()
        th.join(30)


def test_backfill_507(client, ctx, tmp_root, fixtures, monkeypatch):
    did = converted(client, ctx, tmp_root, fixtures)
    _pre_v7(ctx, did)
    real = source_copy.shutil.disk_usage
    monkeypatch.setattr(source_copy.shutil, "disk_usage", lambda p: type(real(p))(1, 1, 5))
    r = client.post("/api/sources/backfill", json={"dry_run": False})
    assert r.status_code == 507 and r.json()["error"] == "insufficient_disk" and r.json()["free"] == 5


def test_retry_endpoint(client, ctx, tmp_root, fixtures, monkeypatch):
    did = converted(client, ctx, tmp_root, fixtures)
    assert client.post("/api/documents/nope/source-copy").status_code == 404
    r = client.post(f"/api/documents/{did}/source-copy")
    assert r.status_code == 200 and r.json()["outcome"] == "already_present"
    _pre_v7(ctx, did)
    real = source_copy.shutil.disk_usage
    monkeypatch.setattr(source_copy.shutil, "disk_usage", lambda p: type(real(p))(1, 1, 5))
    r = client.post(f"/api/documents/{did}/source-copy")
    assert r.status_code == 507 and r.json()["source_copy"]["status"] == "failed"
    monkeypatch.setattr(source_copy.shutil, "disk_usage", real)   # never monkeypatch.undo(): it drops the env too
    r = client.post(f"/api/documents/{did}/source-copy")
    assert r.status_code == 200 and r.json()["outcome"] == "copied"
    view = r.json()["source_copy"]
    assert view["status"] == "copied" and view["present"] and Path(view["path"]).is_file()
    _wait(ctx, "document.source_copy", lambda p: p["document_id"] == did and p["status"] == "copied")
    # sha mismatch: the only candidate (the original) was rewritten
    _pre_v7(ctx, did)
    shutil.rmtree(ctx.store.get_document(did)["work_copy_path"])
    (tmp_root / "text.pdf").write_bytes(b"%PDF-1.4 rewritten")
    r = client.post(f"/api/documents/{did}/source-copy")
    assert r.status_code == 409 and r.json()["error"] == "sha_mismatch"
    (tmp_root / "text.pdf").unlink()
    r = client.post(f"/api/documents/{did}/source-copy")
    assert r.status_code == 410 and r.json()["error"] == "source_missing"
    assert ctx.store.get_document(did)["source_copy_status"] == "unrecoverable"


def test_retry_refused_while_converting(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures)
    client.post("/api/documents/reconvert", json={"ids": [did]})          # queued, not processed
    r = client.post(f"/api/documents/{did}/source-copy")
    assert r.status_code == 409 and r.json()["error"] == "already_converting"


def test_copy_error_is_shown_relative_to_the_doc4ai_folder(client, ctx, tmp_root, fixtures):
    """The Document view shows the real reason (the OS message) but never the absolute Doc4AI folder path."""
    did = converted(client, ctx, tmp_root, fixtures)
    root = str(ctx.config.library_root())
    ctx.store.set_source_copy(did, path=None, status="failed",
                              error=f"io_error: [errno 17] File exists: {root}\\source\\text")
    sc = client.get(f"/api/documents/{did}").json()["source_copy"]
    assert sc["error"] == "io_error: [errno 17] File exists: source\\text" and root not in sc["error"]
