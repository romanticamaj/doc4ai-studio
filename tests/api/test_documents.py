import hashlib
import io
import shutil
import zipfile
from pathlib import Path


def converted(client, ctx, tmp_root, fixtures, name="text.pdf"):
    p = tmp_root / name
    shutil.copy(fixtures / name, p)
    job = client.post("/api/jobs", json={"inputs": [{"path": str(p)}]}).json()["job"]
    ctx.queue.process_next()
    return client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["document_id"]


def test_list_get_markdown_assets(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures)
    docs = client.get("/api/documents").json()["documents"]
    assert docs[0]["id"] == did and docs[0]["stem"] == "text" and "work_copy_path" not in docs[0]
    assert client.get("/api/documents?q=TEXT").json()["documents"]
    assert not client.get("/api/documents?engine=mineru").json()["documents"]
    assert client.get("/api/documents?status=ok").json()["documents"]
    d = client.get(f"/api/documents/{did}").json()
    assert d["source_available"] and d["output_available"] and d["document"]["id"] == did
    md = client.get(f"/api/documents/{did}/markdown")
    assert md.headers["content-type"].startswith("text/markdown") and "<!-- page: 1 -->" in md.text
    assert client.get(f"/api/documents/{did}/assets/p1_1.png").status_code == 200
    assert client.get(f"/api/documents/{did}/assets/nope.png").status_code == 404
    assert client.get("/api/documents/nope").status_code == 404


def test_asset_traversal_rejected(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures)
    for bad in ("..%2F..%2Ftext.json", "..%2Ftext.json", "..%5Ctext.json", "..%2F..%2F..%2Fdata%2Faidoc.db",
                "C:%5CWindows%5Cwin.ini", "%2Fetc%2Fpasswd"):
        r = client.get(f"/api/documents/{did}/assets/{bad}")
        assert r.status_code == 400 and r.json()["error"] == "bad_path", bad


def test_source_and_zip(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures)
    r = client.get(f"/api/documents/{did}/source")
    assert r.status_code == 200 and r.content == (fixtures / "text.pdf").read_bytes()
    z = client.get(f"/api/documents/{did}/download.zip")
    assert z.status_code == 200 and 'filename="text.zip"' in z.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "text.md" in names and "text.json" in names and any(n.startswith("assets/") for n in names)


def test_source_of_uploaded_document_comes_from_work_copy(client, ctx, fixtures):
    data = (fixtures / "text.pdf").read_bytes()
    uid = client.post("/api/uploads", json={"filename": "up.pdf", "size": len(data),
                                            "sha256": hashlib.sha256(data).hexdigest()}).json()["upload_id"]
    client.put(f"/api/uploads/{uid}?offset=0", content=data)
    job = client.post("/api/jobs", json={"inputs": [{"upload_id": uid}]}).json()["job"]
    ctx.queue.process_next()
    did = client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["document_id"]
    r = client.get(f"/api/documents/{did}/source")
    assert r.status_code == 200 and r.content == data


def test_source_missing_and_orphaned(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures)
    doc = ctx.store.get_document(did)
    (tmp_root / "text.pdf").unlink()
    shutil.rmtree(doc["work_copy_path"])
    r = client.get(f"/api/documents/{did}/source")                # the source copy still serves it (spec 2026-10-29)
    assert r.status_code == 200 and r.content == (fixtures / "text.pdf").read_bytes()
    Path(ctx.store.get_document(did)["source_copy_path"]).unlink()
    r = client.get(f"/api/documents/{did}/source")
    assert r.status_code == 410 and r.json()["error"] == "source_missing"
    assert client.get(f"/api/documents/{did}").json()["source_available"] is False
    shutil.rmtree(doc["output_dir"])
    assert client.get(f"/api/documents/{did}").json()["output_available"] is False
    assert client.get(f"/api/documents/{did}/markdown").status_code == 410
    assert client.post("/api/documents/rescan").json()["orphaned"] == 1
    assert client.get("/api/documents?status=orphaned").json()["documents"][0]["id"] == did
    assert client.delete("/api/documents/orphaned").json()["deleted"] == 1
    assert client.get(f"/api/documents/{did}").status_code == 404


def test_rescan_restores_a_document_whose_output_came_back(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures)
    out = Path(ctx.store.get_document(did)["output_dir"])
    shutil.move(str(out), str(tmp_root / "moved"))
    assert client.post("/api/documents/rescan").json()["orphaned"] == 1
    shutil.move(str(tmp_root / "moved"), str(out))
    assert client.post("/api/documents/rescan").json()["orphaned"] == 0
    assert client.get(f"/api/documents/{did}").json()["document"]["status"] == "ok"


def test_rescan_reports_page_flags(client, ctx, tmp_root, fixtures):
    converted(client, ctx, tmp_root, fixtures)
    r = client.post("/api/documents/rescan?all=1").json()
    assert {"orphaned", "assessed", "page_map_incomplete", "page_quality"} <= set(r)
    assert r["assessed"] >= 1 and r["page_map_incomplete"] == 0


def test_document_flags_and_page_summary(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures)
    d = client.get(f"/api/documents/{did}").json()["document"]
    assert d["flags"] == [] and d["page_summary"]["coverage"] == 1.0 and d["page_summary"]["expected"] == 3
    assert client.get("/api/documents?flag=page_map_incomplete").json()["documents"] == []
    assert client.get("/api/documents?flag=bogus").status_code == 422


def test_reconvert_rewrites_same_output_dir(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures)
    doc = client.get(f"/api/documents/{did}").json()["document"]
    r = client.post("/api/documents/reconvert", json={"ids": [did]})
    assert r.status_code == 201
    ctx.queue.process_next()
    tasks = client.get(f"/api/jobs/{r.json()['job']['id']}").json()["tasks"]
    assert tasks[0]["status"] == "done" and tasks[0]["output_dir"] == doc["output_dir"]
    assert "reconvert" not in tasks[0]["flags"]                                      # one-run flags cleared


def test_reconvert_uses_work_copy_when_source_is_gone(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures)
    (tmp_root / "text.pdf").unlink()
    r = client.post("/api/documents/reconvert", json={"ids": [did]})
    assert r.status_code == 201
    ctx.queue.process_next()
    assert client.get(f"/api/jobs/{r.json()['job']['id']}").json()["tasks"][0]["status"] == "done"
    d = client.get(f"/api/documents/{did}").json()
    assert d["source_available"] is True                       # the new work copy serves the Document view


def test_reconvert_errors_create_nothing(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures)
    n_jobs = len(client.get("/api/jobs").json()["jobs"])
    assert client.post("/api/documents/reconvert", json={"ids": [did, "nope"]}).json()["error"] == "not_found"
    assert client.post("/api/documents/reconvert", json={"ids": []}).status_code == 422
    assert len(client.get("/api/jobs").json()["jobs"]) == n_jobs


def test_reconvert_source_missing(client, ctx, tmp_root, fixtures):
    import shutil as _sh
    did = converted(client, ctx, tmp_root, fixtures)
    (tmp_root / "text.pdf").unlink()
    _sh.rmtree(ctx.store.get_document(did)["work_copy_path"])
    Path(ctx.store.get_document(did)["source_copy_path"]).unlink()      # and the source copy (spec 2026-10-29)
    r = client.post("/api/documents/reconvert", json={"ids": [did]})
    assert r.status_code == 410 and r.json()["error"] == "source_missing" and r.json()["id"] == did


def test_reconvert_starts_over_when_a_cancelled_task_is_reused(client, ctx, tmp_root, fixtures):
    """A cancelled reconvert keeps its row (index A14); a new reconvert must not resume its old engine's segments."""
    from doc4ai.models import Attempt
    did = converted(client, ctx, tmp_root, fixtures)
    job = client.post("/api/documents/reconvert", json={"ids": [did]}).json()["job"]
    client.post(f"/api/jobs/{job['id']}/cancel")
    store = ctx.store
    tid = client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["id"]
    for seg in store.list_segments(tid) or [store.get_segment(i) for i in store.create_segments(tid, [(1, 3)])]:
        store.update_segment(seg["id"], status="done")
    store.append_attempt(tid, Attempt(engine="mineru", attempt=1, score=None, reasons=[], error_kind="engine",
                                      error_msg="segment quick check failed"))
    job2 = client.post("/api/documents/reconvert", json={"ids": [did]}).json()["job"]
    t = client.get(f"/api/jobs/{job2['id']}").json()["tasks"][0]
    assert t["id"] == tid and t["tried"] == [] and t["engine"] is None
    assert all(s["status"] == "queued" for s in store.list_segments(tid))


def test_reconvert_keeps_each_documents_own_output_root_and_lang(client, ctx, tmp_root, fixtures):
    """One job for documents from different output roots / languages: each is rewritten in place, in its lang."""
    a = converted(client, ctx, tmp_root, fixtures)
    other = tmp_root / "elsewhere"
    src = tmp_root / "twocol.pdf"
    shutil.copy(fixtures / "twocol.pdf", src)
    job = client.post("/api/jobs", json={"inputs": [{"path": str(src)}], "output_dir": str(other), "lang": "en"}).json()["job"]
    ctx.queue.process_next()
    b = client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["document_id"]
    before = {d: client.get(f"/api/documents/{d}").json()["document"] for d in (a, b)}
    n_docs = len(client.get("/api/documents").json()["documents"])
    r = client.post("/api/documents/reconvert", json={"ids": [a, b]})
    ctx.queue.process_next()
    ctx.queue.process_next()
    tasks = client.get(f"/api/jobs/{r.json()['job']['id']}").json()["tasks"]
    assert [t["status"] for t in tasks] == ["done", "done"]
    assert {t["output_dir"] for t in tasks} == {before[a]["output_dir"], before[b]["output_dir"]}
    assert len(client.get("/api/documents").json()["documents"]) == n_docs              # no new document rows
    assert client.get(f"/api/documents/{b}").json()["document"]["lang"] == "en"


def test_files_revalidate_so_a_reconvert_is_never_shown_from_cache(client, ctx, tmp_root, fixtures):
    """Reconverted outputs reuse names (p1_1.png); without Cache-Control the browser kept the pre-repair image."""
    import os
    import time as _t
    did = converted(client, ctx, tmp_root, fixtures)
    urls = [f"/api/documents/{did}/assets/p1_1.png", f"/api/documents/{did}/markdown", f"/api/documents/{did}/source"]
    first = {}
    for u in urls:
        r = client.get(u)
        assert r.status_code == 200 and "no-cache" in r.headers["cache-control"] and r.headers.get("etag"), u
        first[u] = r.headers["etag"]
        assert client.get(u, headers={"If-None-Match": first[u]}).status_code == 304, u   # unchanged: revalidated
    out = ctx.store.get_document(did)["output_dir"]
    md = os.path.join(out, os.path.basename(out) + ".md")
    _t.sleep(0.05)
    with open(md, "a", encoding="utf-8") as f:
        f.write("\nrepaired\n")
    png = os.path.join(out, "assets", "p1_1.png")
    with open(png, "ab") as f:
        f.write(b"0")
    for u in urls[:2]:                                                       # changed content: new bytes, new tag
        r = client.get(u, headers={"If-None-Match": first[u]})
        assert r.status_code == 200 and r.headers["etag"] != first[u], u
    assert "repaired" in client.get(urls[1]).text


def test_reconvert_from_source_copy_is_a_real_copy(client, ctx, tmp_root, fixtures):
    """spec 2026-10-29 §6.3: a source copy is staged into the new task's work dir as a real copy, never a hardlink
    (editing a file under source/ must not reach the work dir)."""
    import os
    did = converted(client, ctx, tmp_root, fixtures)
    doc = ctx.store.get_document(did)
    copy = ctx.config.source_root() / "text" / "text.pdf"
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(fixtures / "text.pdf", copy)
    ctx.store.set_source_copy(did, path=str(copy), status="copied", error=None, size=copy.stat().st_size,
                              mtime_ns=copy.stat().st_mtime_ns)
    (tmp_root / "text.pdf").unlink()
    shutil.rmtree(doc["work_copy_path"])
    r = client.post("/api/documents/reconvert", json={"ids": [did]})
    assert r.status_code == 201
    task = client.get(f"/api/jobs/{r.json()['job']['id']}").json()["tasks"][0]
    staged = Path(task["work_path"])
    assert staged.is_file() and os.stat(staged).st_nlink == 1 and os.stat(copy).st_nlink == 1
    ctx.queue.process_next()
    assert client.get(f"/api/jobs/{r.json()['job']['id']}").json()["tasks"][0]["status"] == "done"


# ---- spec 2026-10-29 §10.1 (plan Task 7)

def test_detail_source_kind_and_copy(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures)
    d = client.get(f"/api/documents/{did}").json()
    assert d["source_kind"] == "work_copy" and d["source_available"] is True
    sc = d["source_copy"]
    assert sc["status"] == "copied" and sc["present"] is True and sc["error"] is None
    assert Path(sc["path"]) == ctx.config.source_root() / "text" / "text.pdf" and sc["size"] > 0 and sc["copied_at"]
    shutil.rmtree(ctx.store.get_document(did)["work_copy_path"])
    (tmp_root / "text.pdf").unlink()
    d = client.get(f"/api/documents/{did}").json()
    assert d["source_kind"] == "source_copy" and d["source_available"] is True
    Path(sc["path"]).unlink()
    d = client.get(f"/api/documents/{did}").json()
    assert d["source_kind"] is None and d["source_copy"]["present"] is False


def test_list_flag_source_not_copied(client, ctx, tmp_root, fixtures):
    a = converted(client, ctx, tmp_root, fixtures)
    b = converted(client, ctx, tmp_root, fixtures, name="sample.docx")
    rows = {r["id"]: r for r in client.get("/api/documents").json()["documents"]}
    assert rows[a]["source_copy"] == {"status": "copied", "present": True} and "source_not_copied" not in rows[a]["flags"]
    ctx.store.set_source_copy(b, path=None, status="failed", error="no_space: full")
    rows = {r["id"]: r for r in client.get("/api/documents").json()["documents"]}
    assert "source_not_copied" in rows[b]["flags"] and rows[b]["source_copy"] == {"status": "failed", "present": False}
    assert [r["id"] for r in client.get("/api/documents?flag=source_not_copied").json()["documents"]] == [b]


def test_flat_private_fields_absent(client, ctx, tmp_root, fixtures):
    did = converted(client, ctx, tmp_root, fixtures)
    from doc4ai.server.serialize import PRIVATE_DOC_FIELDS
    for doc in (client.get("/api/documents").json()["documents"][0], client.get(f"/api/documents/{did}").json()["document"]):
        assert not set(PRIVATE_DOC_FIELDS) & set(doc)


def test_source_served_from_copy_over_long_path(client, ctx, tmp_root, fixtures):
    """A4: a copy whose path is 300+ characters is served by GET /source (long-path safe end to end)."""
    import os

    from doc4ai import fsops
    did = converted(client, ctx, tmp_root, fixtures)
    doc = ctx.store.get_document(did)
    deep = ctx.config.source_root() / ("d" * 150) / ("f" * 150 + ".pdf")
    assert len(str(deep)) >= 300
    from doc4ai.source_copy import copy_verified
    size, mtime_ns = copy_verified(fixtures / "text.pdf", deep, doc["sha256"])
    os.unlink(doc["source_copy_path"])
    ctx.store.set_source_copy(did, path=str(deep), status="copied", error=None, size=size, mtime_ns=mtime_ns)
    shutil.rmtree(doc["work_copy_path"])
    (tmp_root / "text.pdf").unlink()
    r = client.get(f"/api/documents/{did}/source")
    assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == doc["sha256"]
    assert client.get(f"/api/documents/{did}").json()["source_kind"] == "source_copy"
    assert os.path.isfile(fsops.long_path(deep))


def test_purge_orphaned_deletes_their_copies(client, ctx, tmp_root, fixtures):
    """A9: two orphans, one hardlinked with a live document -> 2 paths deleted, the live copy intact."""
    import os

    from doc4ai.names import file_sha256
    live = converted(client, ctx, tmp_root, fixtures)
    shutil.copy(fixtures / "text.pdf", tmp_root / "copy.pdf")
    job = client.post("/api/jobs", json={"inputs": [{"path": str(tmp_root / "copy.pdf")}]}).json()["job"]
    ctx.queue.process_next()
    twin = client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["document_id"]
    other = converted(client, ctx, tmp_root, fixtures, name="sample.docx")
    live_copy = Path(ctx.store.get_document(live)["source_copy_path"])
    twin_copy = Path(ctx.store.get_document(twin)["source_copy_path"])
    other_copy = Path(ctx.store.get_document(other)["source_copy_path"])
    assert os.stat(live_copy).st_ino == os.stat(twin_copy).st_ino
    other_size = other_copy.stat().st_size
    for d in (twin, other):
        shutil.rmtree(ctx.store.get_document(d)["output_dir"])
    assert client.post("/api/documents/rescan").json()["orphaned"] == 2
    s = client.get("/api/sources").json()["orphaned_copies"]
    # the twin shares its data with the live document: deleting it frees nothing, so it is not in `bytes`
    assert s["count"] == 2 and s["bytes"] == other_size and s["shared"] == 1
    r = client.delete("/api/documents/orphaned").json()
    assert r["deleted"] == 2 and r["source_copies_deleted"] == 2 and r["source_copies_kept"] == []
    assert r["source_copy_bytes"] == other_size            # the twin's data is still the live document's
    assert not twin_copy.exists() and not other_copy.exists() and not other_copy.parent.exists()
    assert file_sha256(live_copy) == ctx.store.get_document(live)["sha256"]
