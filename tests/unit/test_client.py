import os
import shutil
import threading

from aidoc import lockfile as lf
from aidoc import paths
from aidoc.client import ServerClient, find_server
from aidoc.config import load_config
from aidoc.models import ConvertOptions


def test_find_server_reads_lock(tmp_root, monkeypatch):
    monkeypatch.delenv("AIDOC_TOKEN", raising=False)
    assert find_server(load_config()) is None
    lf.acquire_lock(paths.data_dir() / "aidoc.lock",
                    {"pid": os.getpid(), "started_at": 0, "host": "0.0.0.0", "port": 8123, "auth": True})
    c = find_server(load_config())
    assert c.base_url == "http://127.0.0.1:8123" and c.token is None      # a wildcard bind is reached on loopback
    monkeypatch.setenv("AIDOC_TOKEN", "fromenv")                          # the token never comes from the lock
    assert find_server(load_config()).token == "fromenv"
    assert find_server(load_config(), token="explicit").token == "explicit"


def test_batch_to_token_server_without_token_explains(tmp_root, monkeypatch, capsys):
    import httpx

    from aidoc.cli import main

    def reply(request):
        return httpx.Response(401, json={"error": "unauthorized", "workspace": "default"})
    monkeypatch.setattr("aidoc.client.find_server",
                        lambda cfg, token=None: ServerClient("http://x", None, transport=httpx.MockTransport(reply)))
    (tmp_root / "in").mkdir()
    (tmp_root / "in" / "a.md").write_text("# a")
    assert main(["batch", str(tmp_root / "in")]) == 1
    assert "--token" in capsys.readouterr().err


def test_find_server_ignores_stale_lock(tmp_root):
    (paths.data_dir() / "aidoc.lock").write_text('{"pid": 999999, "started_at": 0, "host": "127.0.0.1", '
                                                 '"port": 1, "token": null}')
    assert find_server(load_config()) is None


def test_create_and_follow_job(live_server, ctx, tmp_root, fixtures):
    c = ServerClient(live_server, None)
    shutil.copy(fixtures / "text.pdf", tmp_root / "t.pdf")
    job = c.create_job([tmp_root / "t.pdf"], ConvertOptions(output_dir=tmp_root / "out"))
    assert job["origin"] == "cli"
    events, result, stop = [], [], threading.Event()
    th = threading.Thread(target=lambda: result.append(c.follow_job(job["id"], lambda k, p: events.append(k), stop)),
                          daemon=True)
    th.start()
    ctx.queue.process_next()
    th.join(15)
    assert result == ["done"] and "task.updated" in events


def test_follow_returns_at_once_for_a_finished_job(live_server, ctx, tmp_root, fixtures):
    c = ServerClient(live_server, None)
    shutil.copy(fixtures / "text.pdf", tmp_root / "t.pdf")
    job = c.create_job([tmp_root / "t.pdf"], ConvertOptions(output_dir=tmp_root / "out"))
    ctx.queue.process_next()
    assert c.follow_job(job["id"], lambda k, p: None, threading.Event()) == "done"


def test_batch_forwards_to_running_server(live_server, ctx, tmp_root, fixtures, monkeypatch, capsys):
    from aidoc.cli import main
    inp = tmp_root / "in"
    inp.mkdir()
    for n in ("text.pdf", "sample.docx", "corrupt.pdf"):
        shutil.copy(fixtures / n, inp / n)
    monkeypatch.setattr("aidoc.client.find_server", lambda cfg, token=None: ServerClient(live_server, None))
    ctx.queue.start()
    assert main(["batch", str(inp), "-o", str(tmp_root / "o2")]) == 2      # corrupt.pdf fails
    out = capsys.readouterr().out
    assert f"forwarding to server {live_server}" in out
    assert "done" in out and "text.pdf" in out and "failed" in out and "corrupt.pdf" in out
    jobs = ctx.store.list_jobs()
    assert jobs[0]["origin"] == "cli" and jobs[0]["options"]["output_dir"] == str(tmp_root / "o2")
    assert (tmp_root / "o2" / "_manifest.jsonl").is_file()


def test_ctrl_c_while_following_detaches(live_server, ctx, tmp_root, fixtures, monkeypatch, capsys):
    from aidoc.batch import run_batch_via_server
    shutil.copy(fixtures / "text.pdf", tmp_root / "t.pdf")
    c = ServerClient(live_server, None)

    def interrupted(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(c, "follow_job", interrupted)
    rc = run_batch_via_server(c, [tmp_root / "t.pdf"], ConvertOptions(output_dir=tmp_root / "out"), print)
    out = capsys.readouterr().out
    job_id = ctx.store.list_jobs()[0]["id"]
    assert rc == 0 and f"detached; job {job_id} keeps running (aidoc cancel {job_id} to cancel)" in out
    assert ctx.store.list_tasks(job_id)[0]["status"] == "queued"          # still the server's to run


def test_cancel_command(live_server, ctx, tmp_root, fixtures, monkeypatch, capsys):
    from aidoc.cli import main
    shutil.copy(fixtures / "text.pdf", tmp_root / "t.pdf")
    c = ServerClient(live_server, None)
    job = c.create_job([tmp_root / "t.pdf"], ConvertOptions(output_dir=tmp_root / "out"))
    monkeypatch.setattr("aidoc.client.find_server", lambda cfg, token=None: ServerClient(live_server, None))
    assert main(["cancel", job["id"]]) == 0
    assert ctx.store.get_job(job["id"])["status"] == "cancelled"
    assert main(["cancel", "nope"]) == 1
    monkeypatch.setattr("aidoc.client.find_server", lambda cfg, token=None: None)
    assert main(["cancel", job["id"]]) == 1
    assert "no server running" in capsys.readouterr().err
