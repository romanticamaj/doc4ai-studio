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
