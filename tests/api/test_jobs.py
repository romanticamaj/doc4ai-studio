import json
import shutil
import threading
import time

from tests.fakes.scenario import write_scenario


def make_src(tmp_root, fixtures, name="text.pdf"):
    p = tmp_root / name
    shutil.copy(fixtures / name, p)
    return str(p)


def wait_until(pred, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    raise AssertionError("condition not met")


def test_create_and_run_job(client, ctx, tmp_root, fixtures):
    r = client.post("/api/jobs", json={"inputs": [{"path": make_src(tmp_root, fixtures)}], "lang": "cht"})
    assert r.status_code == 201
    job = r.json()["job"]
    assert job["status"] == "queued" and job["progress"] == {"done": 0, "total": 1} and job["origin"] == "web"
    assert ctx.queue.process_next() is not None
    d = client.get(f"/api/jobs/{job['id']}").json()
    assert d["job"]["status"] == "done" and d["tasks"][0]["status"] == "done" and d["tasks"][0]["engine"] == "docling"
    assert d["tasks"][0]["segments"][0]["status"] == "done" and d["tasks"][0]["document_id"]
    assert d["tasks"][0]["progress"] == {"pages_done": 1, "pages_total": 1} or d["tasks"][0]["progress"]["pages_done"]
    kinds = [e["kind"] for e in ctx.store.events_since(0)]
    assert "task.updated" in kinds and "job.updated" in kinds and kinds.index("task.updated") < kinds.index("job.updated")


def test_events_carry_api_shapes(client, ctx, tmp_root, fixtures):
    job = client.post("/api/jobs", json={"inputs": [{"path": make_src(tmp_root, fixtures)}]}).json()["job"]
    ctx.queue.process_next()
    evs = ctx.store.events_since(0)
    tu = [e["payload"] for e in evs if e["kind"] == "task.updated"]
    assert all("progress" in p and "job_id" in p for p in tu) and tu[-1]["status"] == "done"
    su = [e["payload"] for e in evs if e["kind"] == "segment.updated"]
    assert su and set(su[0]) >= {"id", "idx", "page_start", "page_end", "status", "attempt", "task_id"}
    ju = [e["payload"] for e in evs if e["kind"] == "job.updated"]
    assert ju[-1]["id"] == job["id"] and ju[-1]["status"] == "done" and ju[-1]["progress"] == {"done": 1, "total": 1}
    assert any(e["kind"] == "queue.updated" for e in evs)


def test_list_jobs_and_missing_input(client, tmp_root, fixtures):
    make_src(tmp_root, fixtures)
    r = client.post("/api/jobs", json={"inputs": [{"path": str(tmp_root / "nope.pdf")}]})
    assert r.status_code == 400 and r.json()["error"] == "input_not_found"
    client.post("/api/jobs", json={"inputs": [{"path": str(tmp_root / "text.pdf")}]})
    assert len(client.get("/api/jobs").json()["jobs"]) == 1
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.post("/api/jobs", json={"inputs": []}).status_code == 422
    assert client.post("/api/jobs", json={"inputs": [{"path": "x", "upload_id": "y"}]}).status_code == 422


def test_cancel_running_job(client, ctx, tmp_root, fixtures):
    write_scenario(tmp_root / "sc.json", default="slow_ok", slow_s=30)
    job = client.post("/api/jobs", json={"inputs": [{"path": make_src(tmp_root, fixtures)},
                                                    {"path": make_src(tmp_root, fixtures, "sample.docx")}]}).json()["job"]
    t = threading.Thread(target=ctx.queue.process_next, daemon=True)
    t.start()
    while ctx.queue.running_task_id is None:
        time.sleep(0.05)
    t0 = time.time()
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    t.join(10)
    assert time.time() - t0 < 10
    d = client.get(f"/api/jobs/{job['id']}").json()
    assert {x["status"] for x in d["tasks"]} == {"cancelled"} and d["job"]["status"] == "cancelled"
    assert ctx.queue.process_next() is None


def test_retry_low_and_new_version(client, ctx, tmp_root, fixtures):
    write_scenario(tmp_root / "sc.json", default="low")
    src = make_src(tmp_root, fixtures)
    job = client.post("/api/jobs", json={"inputs": [{"path": src}]}).json()["job"]
    ctx.queue.process_next()
    task = client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]
    assert task["status"] == "low"
    write_scenario(tmp_root / "sc.json")
    r = client.post(f"/api/tasks/{task['id']}/retry", json={"retry_low": True})
    assert r.status_code == 200 and r.json()["task"]["status"] == "queued"
    ctx.queue.process_next()
    t2 = client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]
    assert t2["status"] == "done" and t2["flags"] == {}            # force applies to that one run only
    shutil.copy(fixtures / "twocol.pdf", src)                       # file changed on disk
    r = client.post(f"/api/tasks/{task['id']}/retry", json={"use_new_version": True})
    assert r.json()["task"]["sha256"] != task["sha256"]
    ctx.queue.process_next()
    assert client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["status"] == "done"
    assert client.post("/api/tasks/nope/retry", json={}).status_code == 404


def test_retry_of_cancelled_job_task_runs_again(client, ctx, tmp_root, fixtures):
    job = client.post("/api/jobs", json={"inputs": [{"path": make_src(tmp_root, fixtures)}]}).json()["job"]
    client.post(f"/api/jobs/{job['id']}/cancel")
    tid = client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["id"]
    assert client.get(f"/api/jobs/{job['id']}").json()["job"]["status"] == "cancelled"
    client.post(f"/api/tasks/{tid}/retry", json={})
    assert ctx.queue.process_next() == tid
    d = client.get(f"/api/jobs/{job['id']}").json()
    assert d["tasks"][0]["status"] == "done" and d["job"]["status"] == "done"


def test_pause_resume(client, ctx, tmp_root, fixtures):
    assert client.post("/api/queue/pause").json()["queue"]["paused"] is True
    client.post("/api/jobs", json={"inputs": [{"path": make_src(tmp_root, fixtures)}]})
    assert ctx.queue.process_next() is None                         # paused: nothing runs
    q = client.post("/api/queue/resume").json()["queue"]
    assert q["paused"] is False and q["length"] == 1
    assert ctx.queue.process_next() is not None


def test_pause_waits_for_segment_then_resumes_without_rerun(client, ctx, tmp_root, fixtures):
    sc = write_scenario(tmp_root / "sc.json", default="slow_ok", slow_s=1.5)
    job = client.post("/api/jobs", json={"inputs": [{"path": make_src(tmp_root, fixtures, "big.pdf")}]}).json()["job"]
    t = threading.Thread(target=ctx.queue.process_next, daemon=True)
    t.start()
    tid = client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["id"]
    wait_until(lambda: any(s["status"] == "converting" for s in ctx.store.list_segments(tid)))
    client.post("/api/queue/pause")
    t.join(20)
    segs = ctx.store.list_segments(tid)
    assert ctx.store.get_task(tid)["status"] == "queued" and [s["status"] for s in segs] == ["done", "queued"]
    client.post("/api/queue/resume")
    assert ctx.queue.process_next() == tid and ctx.store.get_task(tid)["status"] == "done"
    calls = [json.loads(line) for line in (tmp_root / "calls.jsonl").read_text().splitlines()]
    assert [c["pages"] for c in calls] == [[1, 40], [41, 45]]       # segment 0 was not converted twice
    assert sc.exists()


def test_stop_requeues_running_task(client, ctx, tmp_root, fixtures):
    write_scenario(tmp_root / "sc.json", default="slow_ok", slow_s=30)
    job = client.post("/api/jobs", json={"inputs": [{"path": make_src(tmp_root, fixtures)}]}).json()["job"]
    ctx.queue.start()
    wait_until(lambda: ctx.queue.running_task_id is not None)
    tid = ctx.queue.running_task_id
    t0 = time.time()
    ctx.queue.stop()
    assert time.time() - t0 < 10
    assert ctx.store.get_task(tid)["status"] == "queued"            # server shutdown is not a user cancel
    assert client.get(f"/api/jobs/{job['id']}").json()["job"]["status"] in ("queued", "running")


def test_worker_thread_runs_jobs(client, ctx, tmp_root, fixtures):
    ctx.queue.start()
    job = client.post("/api/jobs", json={"inputs": [{"path": make_src(tmp_root, fixtures)}]}).json()["job"]
    for _ in range(200):
        if client.get(f"/api/jobs/{job['id']}").json()["job"]["status"] == "done":
            break
        time.sleep(0.1)
    else:
        raise AssertionError("job did not finish")
    ctx.queue.stop()


def test_cancel_during_backoff(client, ctx, tmp_root, fixtures, monkeypatch):
    from aidoc.retry import RetryPolicy
    monkeypatch.setattr("aidoc.pipeline.RETRY_POLICY", RetryPolicy(backoff=(30.0, 30.0)))
    write_scenario(tmp_root / "sc.json", rules=[{"match": {"engine": "docling"}, "behavior": "crash"}])
    job = client.post("/api/jobs", json={"inputs": [{"path": make_src(tmp_root, fixtures)}]}).json()["job"]
    t = threading.Thread(target=ctx.queue.process_next, daemon=True)
    t.start()
    deadline = time.time() + 10               # the crash is recorded as a transient attempt: now in backoff
    while time.time() < deadline:
        tried = ctx.store.list_tasks(job["id"])[0]["tried"]
        if any(a.get("error_kind") == "transient" for a in tried):
            break
        time.sleep(0.05)
    task = ctx.store.list_tasks(job["id"])[0]
    assert any(a.get("error_kind") == "transient" for a in task["tried"]) and task["status"] not in ("cancelled",)
    t0 = time.time()
    client.post(f"/api/jobs/{job['id']}/cancel")
    t.join(5)
    assert time.time() - t0 < 3 and client.get(f"/api/jobs/{job['id']}").json()["tasks"][0]["status"] == "cancelled"


def test_queue_skips_a_task_another_process_is_running(client, ctx, tmp_root, fixtures):
    """A queued task whose liveness lock is held elsewhere (an in-process CLI) is skipped, not spun on."""
    from aidoc.tasklock import TaskLock, lock_path
    job = client.post("/api/jobs", json={"inputs": [{"path": make_src(tmp_root, fixtures)},
                                                    {"path": make_src(tmp_root, fixtures, "sample.docx")}]}).json()["job"]
    first, second = [t["id"] for t in ctx.store.list_tasks(job["id"])]
    other = TaskLock(lock_path(ctx.config.data_dir, first))
    assert other.acquire()
    try:
        assert ctx.queue.process_next() == second
        assert ctx.queue.process_next() is None
        assert ctx.store.get_task(first)["status"] == "queued"
    finally:
        other.release()
    assert ctx.queue.process_next() == first


def test_shutdown_keeps_the_retry_force_flag(client, ctx, tmp_root, fixtures):
    """Final review I2: a retry interrupted by server shutdown must still bypass the cache when it resumes."""
    write_scenario(tmp_root / "sc.json", default="low")
    job = client.post("/api/jobs", json={"inputs": [{"path": make_src(tmp_root, fixtures)}]}).json()["job"]
    ctx.queue.process_next()
    tid = ctx.store.list_tasks(job["id"])[0]["id"]
    write_scenario(tmp_root / "sc.json", default="slow_ok", slow_s=30)
    client.post(f"/api/tasks/{tid}/retry", json={"retry_low": True})
    ctx.queue.start()
    wait_until(lambda: ctx.queue.running_task_id == tid)
    ctx.queue.stop()
    t = ctx.store.get_task(tid)
    assert t["status"] == "queued" and t["flags"] == {"force": True, "auto_engine": True}
