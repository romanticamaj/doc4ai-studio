"""Independent P3 verifier findings, fixed during P4 (each test failed first)."""
import asyncio
import contextlib
import pathlib
import shutil
import subprocess
import sys
import time
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from aidoc.server.app import create_app
from tests.api.test_jobs_inputs import upload


# ---------------------------------------------------------------- #1 SPA route never touches user paths
@pytest.fixture
def web_client(ctx, tmp_root):
    dist = tmp_root / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>spa</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_root / "secret.txt").write_text("top secret", encoding="utf-8")
    with TestClient(create_app(ctx), client=("127.0.0.1", 50000), base_url="http://127.0.0.1:8765") as c:
        yield c


BAD_PATHS = [
    "/%5C%5C10.255.255.1%5Cshare%5Cx",       # \\host\share\x (UNC -> SMB, NTLM leak)
    "/%5C%5C%3F%5CC:%5Cwindows%5Cwin.ini",  # \\?\C:\...
    "/C:/Windows/win.ini",
    "/C:%5CWindows%5Cwin.ini",
    "/assets/..%5C..%5C..%5Csecret.txt",
    "/assets/%2e%2e/%2e%2e/%2e%2e/secret.txt",
    "//10.255.255.1/share/x",
    "/a%00b",
]


@pytest.mark.parametrize("path", BAD_PATHS)
def test_spa_route_never_resolves_user_paths(web_client, monkeypatch, path):
    touched = []
    real_resolve, real_is_file = pathlib.Path.resolve, pathlib.Path.is_file

    def spy_resolve(self, *a, **k):
        touched.append(str(self))
        return real_resolve(self, *a, **k)

    def spy_is_file(self):
        touched.append(str(self))
        return real_is_file(self)
    monkeypatch.setattr(pathlib.Path, "resolve", spy_resolve)
    monkeypatch.setattr(pathlib.Path, "is_file", spy_is_file)
    t0 = time.time()
    r = web_client.get(path)
    assert time.time() - t0 < 2
    assert r.status_code in (200, 404)
    assert "top secret" not in r.text and "[fonts]" not in r.text
    assert not any("10.255.255.1" in p or "share" in p or "win.ini" in p.lower() or "secret" in p for p in touched)


def test_spa_serves_dist_files_and_falls_back_to_index(web_client):
    assert web_client.get("/assets/app.js").text == "console.log(1)"
    r = web_client.get("/jobs/abc")
    assert r.status_code == 200 and "<title>spa</title>" in r.text
    assert web_client.get("/").status_code == 200


def test_spa_routes_check_the_host_without_a_token(web_client):
    r = web_client.get("/", headers={"Host": "evil.example:8765"})
    assert r.status_code == 403 and r.json()["error"] == "bad_host"


def test_api_docs_hidden_when_a_token_is_set(token_ctx):
    with TestClient(create_app(token_ctx)) as c:
        assert c.get("/api/openapi.json").status_code in (401, 404)
        assert c.get("/api/docs").status_code in (401, 404)


def test_get_on_a_post_only_api_route_is_405(web_client):
    r = web_client.get("/api/queue/pause")
    assert r.status_code == 405 and r.json()["error"] == "method_not_allowed"
    assert web_client.get("/api/nope").status_code == 404


# ---------------------------------------------------------------- #2 PUT to a finished upload
def test_put_to_a_complete_upload_is_409_not_500(client, fixtures):
    uid = upload(client, fixtures / "text.pdf")
    data = (fixtures / "text.pdf").read_bytes()
    r = client.put(f"/api/uploads/{uid}?offset={len(data)}", content=b"")
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "upload_not_receiving" and body["status"] == "complete" and body["received"] == len(data)


# ---------------------------------------------------------------- #3 non-Latin-1 names in headers
def _convert(client, ctx, src):
    job = client.post("/api/jobs", json={"inputs": [{"path": str(src)}]}).json()["job"]
    ctx.queue.process_next()
    return client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["document_id"]


def test_download_zip_with_a_chinese_name(client, ctx, fixtures, tmp_root):
    src = tmp_root / "報告 第一版.pdf"
    shutil.copy(fixtures / "text.pdf", src)
    doc_id = _convert(client, ctx, src)
    r = client.get(f"/api/documents/{doc_id}/download.zip")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;") and f"filename*=UTF-8''{quote('報告 第一版.zip')}" in cd
    cd.encode("latin-1")                                     # the header itself is pure ASCII
    s = client.get(f"/api/documents/{doc_id}/source")
    assert s.status_code == 200 and "filename*=" in s.headers["content-disposition"]


# ---------------------------------------------------------------- #6 zip never follows links out of the doc dir
@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_download_zip_skips_junctions(client, ctx, fixtures, tmp_root):
    doc_id = _convert(client, ctx, fixtures / "text.pdf")
    out = pathlib.Path(ctx.store.get_document(doc_id)["output_dir"])
    outside = tmp_root / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("private", encoding="utf-8")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(out / "link"), str(outside)], check=True, capture_output=True)
    import io
    import zipfile
    r = client.get(f"/api/documents/{doc_id}/download.zip")
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert not any("private" in n for n in names) and "text.md" in names


# ---------------------------------------------------------------- #8 small ones
def test_nul_in_asset_path_is_400(client, ctx, fixtures):
    doc_id = _convert(client, ctx, fixtures / "text.pdf")
    assert client.get(f"/api/documents/{doc_id}/assets/a%00b.png").status_code == 400


def test_relative_input_paths_are_rejected(client, fixtures, monkeypatch):
    monkeypatch.chdir(fixtures)
    r = client.post("/api/jobs", json={"inputs": [{"path": "text.pdf"}]})
    assert r.status_code == 400 and r.json()["error"] == "path_not_absolute"


# ---------------------------------------------------------------- #7 settings validation
@pytest.mark.parametrize("body", [
    {"server": {"port": 0}}, {"server": {"port": 70000}},
    {"limits": {"disk_space_factor": 0}}, {"limits": {"upload_max_bytes": 10 ** 30}},
    {"general": {"output_dir": "\\\\server\\share\\out"}}, {"general": {"output_dir": "//server/share"}},
])
def test_settings_rejects_bad_values(client, body):
    r = client.put("/api/settings", json=body)
    assert r.status_code == 422 and r.json()["error"] == "invalid_settings"


def test_settings_server_host_is_read_only_and_masked_token_is_ignored(client, ctx):
    r = client.put("/api/settings", json={"server": {"host": "0.0.0.0"}})
    assert r.status_code == 403 and r.json()["error"] == "server_readonly"
    ok = client.put("/api/settings", json={"general": {"lang": "en"}, "server": {"token": ""}})
    assert ok.status_code == 200 and ctx.config.general.lang == "en"
    ok = client.put("/api/settings", json={"server": {"token": "***"}})
    assert ok.status_code == 200


def test_settings_deeply_nested_json_is_4xx(client):
    body = "[" * 100000 + "]" * 100000
    r = client.put("/api/settings", content=body, headers={"Content-Type": "application/json"})
    assert 400 <= r.status_code < 500


# ---------------------------------------------------------------- #9 Last-Event-ID above the current max
def test_last_event_id_above_max_resyncs(ctx):
    from aidoc.server.sse import replay_plan
    ctx.bus.publish("x", None, {"i": 1})
    top = ctx.store.events_since(0)[-1]["seq"]
    assert replay_plan(ctx.store, top + 50)[0] == "resync"
    assert replay_plan(ctx.store, top)[0] == "replay"


# ---------------------------------------------------------------- SSE: overflow really sends resync; no thread polling
def test_overflowing_subscriber_receives_a_resync_frame(ctx):
    from aidoc.server.sse import RESYNC_FRAME, event_frames

    async def run():
        sub = ctx.bus.subscribe(maxsize=3)
        for i in range(6):
            ctx.bus.publish("x", None, {"i": i})
        frames = event_frames(ctx.bus, ctx.store, sub, None, disconnected=lambda: _false())
        first = await asyncio.wait_for(frames.__anext__(), 2)
        await frames.aclose()
        return first

    async def _false():
        return False
    assert asyncio.run(run()) == RESYNC_FRAME


def test_sse_payloads_carry_workspace(ctx):
    from aidoc.server.sse import format_event
    assert '"workspace": "default"' in format_event(1, "x", {"a": 1})


def test_many_idle_streams_do_not_slow_uploads(live_server, ctx, fixtures):
    streams, clients = [], []
    try:
        for _ in range(60):
            c = httpx.Client(base_url=live_server, timeout=10)
            cm = c.stream("GET", "/api/events")
            cm.__enter__()
            clients.append(c)
            streams.append(cm)
        deadline = time.time() + 10
        while ctx.bus.subscriber_count < 60 and time.time() < deadline:
            time.sleep(0.05)
        data = b"x" * (1024 * 1024)
        import hashlib
        with httpx.Client(base_url=live_server, timeout=10) as c:
            uid = c.post("/api/uploads", json={"filename": "a.bin", "size": len(data),
                                               "sha256": hashlib.sha256(data).hexdigest()}).json()["upload_id"]
            t0 = time.time()
            assert c.put(f"/api/uploads/{uid}?offset=0", content=data).status_code == 200
            assert time.time() - t0 < 1.0
    finally:
        for cm in streams:
            with contextlib.suppress(Exception):
                cm.__exit__(None, None, None)
        for c in clients:
            c.close()


# ---------------------------------------------------------------- #4 lock liveness does not guess from the command line
def test_lock_owner_answering_http_is_live_even_with_an_unusual_cmdline(live_server, monkeypatch):
    from aidoc import lockfile
    port = int(live_server.rsplit(":", 1)[1])
    monkeypatch.setattr(lockfile, "is_aidoc_process", lambda pid: False)     # e.g. python -c "...main(['serve'])"
    import os
    assert lockfile.is_live({"pid": os.getpid(), "host": "127.0.0.1", "port": port}) is True
    assert lockfile.is_live({"pid": os.getpid(), "host": "127.0.0.1", "port": 1}) is False


# ---------------------------------------------------------------- spec §6 page 1: a local *folder* path
def test_a_folder_path_expands_to_its_files(client, ctx, fixtures, tmp_root):
    d = tmp_root / "in"
    (d / "sub").mkdir(parents=True)
    shutil.copy(fixtures / "text.pdf", d / "a.pdf")
    shutil.copy(fixtures / "sample.docx", d / "sub" / "b.docx")
    (d / ".hidden.pdf").write_bytes(b"x")
    r = client.post("/api/jobs", json={"inputs": [{"path": str(d)}]})
    assert r.status_code == 201
    names = sorted(pathlib.Path(t["source_path"]).name for t in ctx.store.list_tasks(r.json()["job"]["id"]))
    assert names == ["a.pdf", "b.docx"]


def test_an_empty_folder_is_400(client, tmp_root):
    (tmp_root / "empty").mkdir()
    r = client.post("/api/jobs", json={"inputs": [{"path": str(tmp_root / "empty")}]})
    assert r.status_code == 400 and r.json()["error"] == "no_inputs"


# ---------------------------------------------------------------- P4 acceptance row 19: 重轉低品質 uses auto routing
def test_retry_low_drops_a_forced_engine_for_that_run(client, ctx, fixtures, tmp_root):
    from tests.fakes.scenario import write_scenario
    write_scenario(tmp_root / "sc.json", rules=[{"match": {"engine": "markitdown"}, "behavior": "low"}])
    src = tmp_root / "s.pdf"
    shutil.copy(fixtures / "text.pdf", src)
    job = client.post("/api/jobs", json={"inputs": [{"path": str(src)}], "engine": "markitdown"}).json()["job"]
    ctx.queue.process_next()
    task = client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]
    assert task["status"] == "low" and task["engine"] == "markitdown"
    client.post(f"/api/tasks/{task['id']}/retry", json={"retry_low": True})
    ctx.queue.process_next()
    t2 = client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]
    assert t2["status"] == "done" and t2["engine"] != "markitdown" and t2["flags"] == {}
    # a plain retry keeps the job's forced engine
    client.post(f"/api/tasks/{task['id']}/retry", json={})
    ctx.queue.process_next()
    assert client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["engine"] == "markitdown"
