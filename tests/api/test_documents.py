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
    r = client.post("/api/documents/reconvert", json={"ids": [did]})
    assert r.status_code == 410 and r.json()["error"] == "source_missing" and r.json()["id"] == did


def test_reconvert_starts_over_when_a_cancelled_task_is_reused(client, ctx, tmp_root, fixtures):
    """A cancelled reconvert keeps its row (index A14); a new reconvert must not resume its old engine's segments."""
    from aidoc.models import Attempt
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
