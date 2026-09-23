import hashlib
import shutil

from fastapi.testclient import TestClient

from aidoc.server.app import create_app


def upload(client, path, name=None):
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    uid = client.post("/api/uploads", json={"filename": name or path.name, "size": len(data),
                                            "sha256": sha}).json()["upload_id"]
    client.put(f"/api/uploads/{uid}?offset=0", content=data)
    return uid


def test_job_from_upload(client, ctx, fixtures, tmp_root):
    uid = upload(client, fixtures / "text.pdf")
    job = client.post("/api/jobs", json={"inputs": [{"upload_id": uid}]}).json()["job"]
    task = client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]
    assert task["source_path"] == "text.pdf" and not ctx.uploads.part_path(uid).exists()
    assert ctx.store.get_upload(uid)["status"] == "consumed"
    ctx.queue.process_next()
    assert client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["status"] == "done"
    assert (tmp_root / "out" / "text" / "text.md").exists()
    assert client.post("/api/jobs", json={"inputs": [{"upload_id": uid}]}).status_code == 409   # consumed


def test_upload_task_never_reads_a_same_named_file_from_cwd(client, ctx, fixtures, tmp_root, monkeypatch):
    """The display name of an upload is not a path: a damaged work copy fails, it is not re-staged from ./name."""
    monkeypatch.chdir(tmp_root)
    shutil.copy(fixtures / "twocol.pdf", tmp_root / "text.pdf")      # a different file with the same name in cwd
    uid = upload(client, fixtures / "text.pdf")
    job = client.post("/api/jobs", json={"inputs": [{"upload_id": uid}]}).json()["job"]
    task = client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]
    from pathlib import Path
    Path(task["work_path"]).write_bytes(b"damaged")
    ctx.queue.process_next()
    t = client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]
    assert t["status"] == "failed" and "source_changed" in t["error_msg"]


def test_upload_filename_is_never_a_path(client, ctx, fixtures, tmp_root):
    uid = upload(client, fixtures / "text.pdf", name="..\\..\\evil/../text.pdf")
    assert ctx.store.get_upload(uid)["filename"] == "text.pdf"


def test_incomplete_upload_rejected(client, fixtures):
    data = (fixtures / "text.pdf").read_bytes()
    uid = client.post("/api/uploads", json={"filename": "t.pdf", "size": len(data) + 5,
                                            "sha256": "0" * 64}).json()["upload_id"]
    client.put(f"/api/uploads/{uid}?offset=0", content=data)
    r = client.post("/api/jobs", json={"inputs": [{"upload_id": uid}]})
    assert r.status_code == 409 and r.json()["error"] == "upload_incomplete"
    assert r.json()["received"] == len(data) and r.json()["size"] == len(data) + 5
    assert client.post("/api/jobs", json={"inputs": [{"upload_id": "nope"}]}).status_code == 404


def test_local_path_forbidden_for_remote_without_token(ctx, tmp_root, fixtures):
    shutil.copy(fixtures / "text.pdf", tmp_root / "t.pdf")
    with TestClient(create_app(ctx), client=("10.0.0.7", 5555), base_url="http://127.0.0.1:8765") as c:
        r = c.post("/api/jobs", json={"inputs": [{"path": str(tmp_root / "t.pdf")}]})
        assert r.status_code == 403 and r.json()["error"] == "local_path_forbidden"
        r = c.post("/api/jobs", json={"inputs": [{"path": str(tmp_root / "nope.pdf")}]})
        assert r.status_code == 403                                   # no existence oracle for remote clients


def test_remote_upload_job_cannot_choose_output_dir(ctx, tmp_root, fixtures):
    with TestClient(create_app(ctx), client=("10.0.0.7", 5555), base_url="http://127.0.0.1:8765") as c:
        uid = upload(c, fixtures / "text.pdf")
        r = c.post("/api/jobs", json={"inputs": [{"upload_id": uid}], "output_dir": str(tmp_root / "elsewhere")})
        assert r.status_code == 403
        assert c.post("/api/jobs", json={"inputs": [{"upload_id": uid}]}).status_code == 201


def test_local_path_allowed_for_remote_with_token(token_ctx, tmp_root, fixtures):
    shutil.copy(fixtures / "text.pdf", tmp_root / "t.pdf")
    with TestClient(create_app(token_ctx), client=("10.0.0.7", 5555)) as c:
        r = c.post("/api/jobs", json={"inputs": [{"path": str(tmp_root / "t.pdf")}]},
                   headers={"Authorization": "Bearer s3cret"})
        assert r.status_code == 201


def test_disk_guard(client, tmp_root, fixtures, monkeypatch):
    shutil.copy(fixtures / "text.pdf", tmp_root / "t.pdf")
    import collections
    monkeypatch.setattr("aidoc.server.api.jobs.shutil.disk_usage",
                        lambda p: collections.namedtuple("u", "total used free")(1, 1, 10))
    r = client.post("/api/jobs", json={"inputs": [{"path": str(tmp_root / "t.pdf")}]})
    assert r.status_code == 507 and r.json()["error"] == "insufficient_disk" and r.json()["free"] == 10
    assert r.json()["needed"] == 3 * (tmp_root / "t.pdf").stat().st_size
    assert client.get("/api/jobs").json()["jobs"] == []
