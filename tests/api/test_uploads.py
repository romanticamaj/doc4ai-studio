import hashlib
import os
import time

from aidoc.server.uploads import CHUNK_SIZE


def start(client, data, name="big.bin"):
    sha = hashlib.sha256(data).hexdigest()
    r = client.post("/api/uploads", json={"filename": name, "size": len(data), "sha256": sha})
    assert r.status_code == 201
    assert r.json()["chunk_size"] == CHUNK_SIZE and r.json()["received"] == 0
    return r.json()["upload_id"], sha


def test_full_upload_in_chunks(client, ctx):
    data = os.urandom(CHUNK_SIZE + 12345)
    uid, sha = start(client, data)
    for off in range(0, len(data), CHUNK_SIZE):
        r = client.put(f"/api/uploads/{uid}?offset={off}", content=data[off:off + CHUNK_SIZE])
        assert r.status_code == 200, r.text
    assert r.json()["status"] == "complete" and ctx.store.get_upload(uid)["received"] == len(data)
    assert (ctx.uploads.part_path(uid)).stat().st_size == len(data)
    ev = [e["payload"] for e in ctx.store.events_since(0) if e["kind"] == "upload.updated"]
    assert ev[-1] == {"id": uid, "received": len(data), "status": "complete"}


def test_resume_after_interruption_and_duplicate_chunk(client, ctx):
    data = os.urandom(3 * CHUNK_SIZE)
    uid, sha = start(client, data)
    client.put(f"/api/uploads/{uid}?offset=0", content=data[:CHUNK_SIZE])
    h = client.head(f"/api/uploads/{uid}")
    assert h.headers["Upload-Offset"] == str(CHUNK_SIZE) and h.headers["Upload-Length"] == str(len(data))
    r = client.put(f"/api/uploads/{uid}?offset=0", content=data[:CHUNK_SIZE])   # retry of a chunk already stored
    assert r.status_code == 409 and r.json()["error"] == "bad_offset" and r.json()["received"] == CHUNK_SIZE
    assert ctx.uploads.part_path(uid).stat().st_size == CHUNK_SIZE              # no duplicate bytes
    r = client.put(f"/api/uploads/{uid}?offset={2 * CHUNK_SIZE}", content=data[2 * CHUNK_SIZE:])   # skipping ahead
    assert r.status_code == 409
    off = int(client.head(f"/api/uploads/{uid}").headers["Upload-Offset"])
    while off < len(data):
        r = client.put(f"/api/uploads/{uid}?offset={off}", content=data[off:off + CHUNK_SIZE])
        off = r.json()["received"]
    assert r.json()["status"] == "complete"
    assert hashlib.sha256(ctx.uploads.part_path(uid).read_bytes()).hexdigest() == sha


def test_sha_mismatch_deletes(client, ctx):
    data = b"x" * 1000
    r = client.post("/api/uploads", json={"filename": "a.pdf", "size": 1000, "sha256": "0" * 64})
    uid = r.json()["upload_id"]
    r = client.put(f"/api/uploads/{uid}?offset=0", content=data)
    assert r.status_code == 422 and r.json()["error"] == "sha_mismatch" and not ctx.uploads.part_path(uid).exists()
    assert ctx.store.get_upload(uid)["status"] == "failed"


def test_limits(client, ctx):
    ctx.config.limits.upload_max_bytes = 1000
    r = client.post("/api/uploads", json={"filename": "a", "size": 2000, "sha256": "0" * 64})
    assert r.status_code == 413 and r.json()["workspace"] == "default"
    uid, _ = start(client, b"y" * 10)
    assert client.put(f"/api/uploads/{uid}?offset=0", content=b"z" * (CHUNK_SIZE + 1)).status_code == 413
    assert client.put(f"/api/uploads/{uid}?offset=0", content=b"z" * 11).status_code == 400   # past declared size
    assert client.head("/api/uploads/nope").status_code == 404
    assert client.put("/api/uploads/nope?offset=0", content=b"z").status_code == 404
    assert client.post("/api/uploads", json={"filename": "a", "size": 1, "sha256": "nothex"}).status_code == 422


def test_upload_disk_guard(client, monkeypatch):
    import collections
    monkeypatch.setattr("aidoc.server.uploads.shutil.disk_usage",
                        lambda p: collections.namedtuple("u", "total used free")(1, 1, 10))
    r = client.post("/api/uploads", json={"filename": "a.pdf", "size": 100, "sha256": "0" * 64})
    assert r.status_code == 507 and r.json()["error"] == "insufficient_disk" and r.json()["needed"] == 300


def test_reconcile_and_purge(client, ctx):
    data = os.urandom(2 * CHUNK_SIZE)
    uid, _ = start(client, data)
    client.put(f"/api/uploads/{uid}?offset=0", content=data[:CHUNK_SIZE])
    ctx.store.update_upload(uid, received=CHUNK_SIZE + 500)               # DB ahead of disk (crash between write and commit)
    ctx.uploads.reconcile()
    assert ctx.store.get_upload(uid)["received"] == CHUNK_SIZE
    ctx.store.update_upload(uid, received=100)                            # disk ahead of DB: truncated to the DB value
    ctx.uploads.reconcile()
    assert ctx.uploads.part_path(uid).stat().st_size == 100
    ctx.store.update_upload(uid, created_at=time.time() - 90000)
    assert ctx.uploads.purge_stale() == 1 and not ctx.uploads.part_path(uid).exists()
    assert ctx.store.get_upload(uid) is None
