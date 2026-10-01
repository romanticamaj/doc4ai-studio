import json
import os
import time

from fastapi.testclient import TestClient

from aidoc import paths
from aidoc.cli import main
from aidoc.server.app import create_app


def test_refuses_public_bind_without_token(tmp_root, capsys):
    assert main(["serve", "--host", "0.0.0.0"]) == 2
    assert "refusing to bind 0.0.0.0 without --token" in capsys.readouterr().err
    assert not (paths.data_dir() / "aidoc.lock").exists()


def test_serve_acquires_lock_and_recovers(tmp_root, monkeypatch, capsys):
    calls = {}

    def fake_run(app, host, port):
        info = json.loads((paths.data_dir() / "aidoc.lock").read_text(encoding="utf-8"))
        calls.update(host=host, port=port, lock=info, routes=set(app.openapi()["paths"]))
    monkeypatch.setattr("aidoc.cli._run_uvicorn", fake_run)
    assert main(["serve", "--port", "8999"]) == 0
    assert calls["host"] == "127.0.0.1" and calls["port"] == 8999
    assert calls["lock"]["pid"] == os.getpid() and calls["lock"]["port"] == 8999
    assert set(calls["lock"]) == {"pid", "started_at", "host", "port", "auth"} and calls["lock"]["auth"] is False
    assert "/api/jobs" in calls["routes"]
    assert not (paths.data_dir() / "aidoc.lock").exists()            # released on exit


def test_serve_runs_recovery(tmp_root, monkeypatch, capsys):
    from pathlib import Path

    from aidoc.models import ConvertOptions
    from aidoc.store import Store
    s = Store(tmp_root / "data" / "aidoc.db")
    job = s.create_job(ConvertOptions(output_dir=Path(tmp_root / "out")), "web")
    tid, _ = s.create_task(job, str(tmp_root / "a.pdf"), "a" * 64, 1, 1.0, "cht", str(tmp_root / "out" / "a"))
    s.update_task(tid, status="converting")
    s.close()
    monkeypatch.setattr("aidoc.cli._run_uvicorn", lambda app, host, port: None)
    assert main(["serve"]) == 0
    assert "requeued 1 interrupted task" in capsys.readouterr().err
    s = Store(tmp_root / "data" / "aidoc.db")
    assert s.get_task(tid)["status"] in ("queued", "failed", "done")   # requeued (the queue may have run it)
    s.close()


def test_second_server_refused(tmp_root, monkeypatch, capsys):
    from aidoc import lockfile as lf
    lf.acquire_lock(paths.data_dir() / "aidoc.lock",
                    {"pid": os.getpid(), "started_at": 0, "host": "127.0.0.1", "port": 1, "token": None})
    monkeypatch.setattr("aidoc.cli._run_uvicorn", lambda app, host, port: None)
    assert main(["serve"]) == 3
    assert "another aidoc server is running on 127.0.0.1:1" in capsys.readouterr().err


def test_token_server_keeps_token_out_of_lock(tmp_root, monkeypatch):
    seen = {}
    monkeypatch.setattr("aidoc.cli._run_uvicorn", lambda app, host, port: seen.update(
        lock=json.loads((paths.data_dir() / "aidoc.lock").read_text(encoding="utf-8")), token=app.state.ctx.token))
    assert main(["serve", "--host", "0.0.0.0", "--token", "abc"]) == 0
    assert "token" not in seen["lock"] and seen["lock"]["auth"] is True and seen["token"] == "abc"
    assert seen["lock"]["host"] == "0.0.0.0"


def test_serve_token_from_env(tmp_root, monkeypatch):
    seen = {}
    monkeypatch.setenv("AIDOC_TOKEN", "fromenv")
    monkeypatch.setattr("aidoc.cli._run_uvicorn", lambda app, host, port: seen.update(token=app.state.ctx.token))
    assert main(["serve", "--host", "0.0.0.0"]) == 0 and seen["token"] == "fromenv"


def test_static_fallback(client, ctx, tmp_root):
    r = client.get("/")
    assert r.status_code == 200 and "web UI not built" in r.text
    dist = tmp_root / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>ui</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    with TestClient(create_app(ctx), base_url="http://127.0.0.1:8765") as c:
        assert c.get("/").text == "<html>ui</html>" and c.get("/library").text == "<html>ui</html>"
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/api/nope").status_code == 404 and c.get("/api/nope").json()["error"] == "not_found"
        assert c.get("/..%2F..%2Fdata%2Faidoc.db").text == "<html>ui</html>"       # never outside dist


def test_maintenance_run_once(ctx, tmp_root):
    from aidoc.server.maintenance import Maintenance
    for _ in range(30):
        ctx.store.append_event("x", None, {})
    uid = ctx.uploads.create("a.pdf", 10, "0" * 64)["id"]
    ctx.store.update_upload(uid, created_at=time.time() - 90000)
    orphan = ctx.config.data_dir / "work" / "deadbeef"                  # a work dir no task knows about
    orphan.mkdir(parents=True)
    (orphan / "src.pdf").write_bytes(b"x")
    old = time.time() - 30 * 86400
    os.utime(orphan, (old, old))
    m = Maintenance(ctx, interval_s=600, keep_events=10)
    res = m.run_once()
    assert res["uploads_purged"] == 1 and res["work_dirs_removed"] == 1 and not orphan.exists()
    assert len(ctx.store.events_since(0)) <= 12
    assert any(e["kind"] == "system.updated" for e in ctx.store.events_since(0))


def test_maintenance_keeps_work_of_unfinished_tasks(client, ctx, tmp_root, fixtures):
    import hashlib

    from aidoc.server.maintenance import Maintenance
    data = (fixtures / "text.pdf").read_bytes()
    uid = client.post("/api/uploads", json={"filename": "t.pdf", "size": len(data),
                                            "sha256": hashlib.sha256(data).hexdigest()}).json()["upload_id"]
    client.put(f"/api/uploads/{uid}?offset=0", content=data)
    job = client.post("/api/jobs", json={"inputs": [{"upload_id": uid}]}).json()["job"]
    task = client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]
    work = ctx.config.data_dir / "work" / task["id"]
    old = time.time() - 30 * 86400
    os.utime(work, (old, old))
    Maintenance(ctx).run_once()
    assert (work / "src.pdf").exists()                                   # queued upload task: never purged


def test_port_in_use_is_a_clear_error_not_exit_3(tmp_root, capsys):
    """uvicorn exits with its own code 3 when it cannot bind, which clashed with "another server" (3)."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert main(["serve", "--port", str(port)]) == 4
    finally:
        s.close()
    assert f"could not listen on 127.0.0.1:{port}" in capsys.readouterr().err
    assert not (paths.data_dir() / "aidoc.lock").exists()


def test_ctrl_c_stops_the_server_cleanly(tmp_root, monkeypatch, capsys):
    def interrupted(app, host, port):
        raise KeyboardInterrupt                     # uvicorn re-raises the captured Ctrl+C / Ctrl+Break on exit
    monkeypatch.setattr("aidoc.cli._run_uvicorn", interrupted)
    assert main(["serve"]) == 0
    err = capsys.readouterr().err
    assert "server stopped" in err and "conversion was cancelled" not in err
    assert not (paths.data_dir() / "aidoc.lock").exists()


def test_expired_work_copy_of_a_queued_retry_is_kept(client, ctx, tmp_root, fixtures):
    """Final review I5: retention must not delete the only copy of an upload whose task was retried."""
    import hashlib

    from aidoc.sources import purge_expired_work_copies
    data = (fixtures / "text.pdf").read_bytes()
    uid = client.post("/api/uploads", json={"filename": "t.pdf", "size": len(data),
                                            "sha256": hashlib.sha256(data).hexdigest()}).json()["upload_id"]
    client.put(f"/api/uploads/{uid}?offset=0", content=data)
    job = client.post("/api/jobs", json={"inputs": [{"upload_id": uid}]}).json()["job"]
    ctx.queue.process_next()
    task = ctx.store.list_tasks(job["id"])[0]
    doc = ctx.store.find_document(task["sha256"], task["output_dir"])
    client.post(f"/api/tasks/{task['id']}/retry", json={})                  # queued again, same work dir
    ctx.store.update_document(doc["id"], work_copy_expires_at=time.time() - 1)
    assert purge_expired_work_copies(ctx.store, time.time()) == 0
    assert (ctx.config.data_dir / "work" / task["id"] / "src.pdf").exists()
    ctx.queue.process_next()
    assert ctx.store.get_task(task["id"])["status"] == "done"


def test_serve_refuses_while_an_in_process_cli_run_is_active(tmp_root, monkeypatch, capsys):
    """Final review I3: one queue owner — an in-process batch/convert and the server must not both convert."""
    from aidoc import clilock
    held = clilock.CliRunLock(paths.data_dir())
    assert held.acquire()
    monkeypatch.setattr("aidoc.cli._run_uvicorn", lambda app, host, port: None)
    try:
        assert main(["serve"]) == 3
        assert "in-process aidoc run" in capsys.readouterr().err
    finally:
        held.release()
    assert main(["serve"]) == 0


def test_in_process_convert_holds_the_cli_run_lock(tmp_root, fixtures, monkeypatch):
    import shutil

    import aidoc.pipeline
    from aidoc import clilock
    from tests.fakes.scenario import fake_env, write_scenario
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    real, seen = aidoc.pipeline.run_task, []

    def spy(*a, **k):
        seen.append(clilock.active_cli_runs(paths.data_dir()))
        return real(*a, **k)
    monkeypatch.setattr(aidoc.pipeline, "run_task", spy)
    shutil.copy(fixtures / "text.pdf", tmp_root / "t.pdf")
    assert main(["convert", str(tmp_root / "t.pdf")]) == 0
    assert seen == [1] and clilock.active_cli_runs(paths.data_dir()) == 0


def test_maintenance_prunes_mcp_calls(ctx):
    import time

    from aidoc.server.maintenance import Maintenance
    old = time.time() - 40 * 86400
    for i in range(5):
        ctx.store.insert_mcp_call(ts=old + i, status="ok", method="tools/list")
    for i in range(3):
        ctx.store.insert_mcp_call(status="ok", method="tools/list")
    ctx.config.mcp.call_log_retention_days = 30
    ctx.config.mcp.call_log_max_rows = 2
    res = Maintenance(ctx).run_once()
    assert res["mcp_calls_pruned"] == 6                      # 5 by age + 1 by the row cap
    assert ctx.store.count_mcp_calls(since=0) == 2


def test_serve_refuses_a_damaged_secret_key(tmp_root, monkeypatch, capsys):
    (paths.data_dir() / "secret.key").write_bytes(b"short")
    monkeypatch.setattr("aidoc.cli._run_uvicorn", lambda app, host, port: None)
    assert main(["serve"]) == 5
    err = capsys.readouterr().err
    assert "secret.key" in err and "5 bytes" in err and "re-issue" in err
    assert (paths.data_dir() / "secret.key").read_bytes() == b"short"
    assert not (paths.data_dir() / "aidoc.lock").exists()
