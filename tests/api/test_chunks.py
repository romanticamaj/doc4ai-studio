import json
import shutil
from pathlib import Path


def _doc(client, ctx, tmp_root, fixtures):
    p = tmp_root / "text.pdf"
    shutil.copy(fixtures / "text.pdf", p)
    job = client.post("/api/jobs", json={"inputs": [{"path": str(p)}]}).json()["job"]
    ctx.queue.process_next()
    return client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["document_id"]


def test_chunks_endpoint(client, ctx, tmp_root, fixtures):
    did = _doc(client, ctx, tmp_root, fixtures)
    r = client.post("/api/chunks", json={"document_id": did, "max_tokens": 50})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in r.text.splitlines()]
    assert len(lines) == int(r.headers["X-Chunk-Count"]) >= 2
    assert lines[0]["source"] == "text.pdf" and lines[0]["page_start"] == 1
    assert client.post("/api/chunks", json={"document_id": "nope"}).status_code == 404
    assert client.post("/api/chunks", json={"document_id": did, "max_tokens": 0}).status_code == 422


def test_chunks_output_missing_and_tokenizer_unavailable(client, ctx, tmp_root, fixtures, monkeypatch):
    did = _doc(client, ctx, tmp_root, fixtures)
    import aidoc.chunk
    from aidoc.chunk import TokenizerUnavailable

    def boom(text):
        raise TokenizerUnavailable("not cached; run aidoc setup")
    monkeypatch.setattr(aidoc.chunk, "count_tokens", boom)
    r = client.post("/api/chunks", json={"document_id": did})
    assert r.status_code == 503 and r.json()["error"] == "tokenizer_unavailable"
    shutil.rmtree(Path(ctx.store.get_document(did)["output_dir"]))
    r = client.post("/api/chunks", json={"document_id": did})
    assert r.status_code == 409 and r.json()["error"] == "output_missing"
