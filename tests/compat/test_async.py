import io
import time

from tests.compat.conftest import last_call
from tests.compat.test_sync import PDF, _post_file


def _submit(env, raw, name="a.pdf", data=PDF, **form):
    return env.client.post("/v1/convert/file/async", files={"files": (name, io.BytesIO(data), "application/pdf")},
                           data=form, headers={"X-Api-Key": raw})


def test_async_lifecycle(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=1.0)
    raw, _ = env.issue()
    r = _submit(env, raw, md_page_break_placeholder="\f")
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["task_type"] == "convert" and st["task_status"] in ("pending", "started") and st["task_position"] == 0
    assert st["task_meta"]["num_docs"] == 1
    tid = st["task_id"]
    assert last_call(env.ctx)["tool_name"] == "convert_file_async" and last_call(env.ctx)["job_id"] == tid
    r = env.client.get(f"/v1/result/{tid}", headers={"X-Api-Key": raw})
    assert r.status_code == 404 and r.json()["detail"] == "task not finished"
    r = env.client.get(f"/v1/status/poll/{tid}", params={"wait": 5}, headers={"X-Api-Key": raw})
    assert r.json()["task_status"] == "success" and r.json()["task_meta"]["pages_total"] >= 1
    assert r.json()["task_meta"]["num_succeeded"] == 1 and last_call(env.ctx)["tool_name"] == "status_poll"
    res = env.client.get(f"/v1/result/{tid}", headers={"X-Api-Key": raw}).json()
    assert res["status"] == "success" and res["document"]["md_content"].count("\f") == res["document"]["doc4ai"]["pages"] - 1
    assert res["document"]["filename"] == "a.pdf"
    assert last_call(env.ctx)["tool_name"] == "result"


def test_source_async(workers):
    import base64
    env = workers
    raw, _ = env.issue()
    r = env.client.post("/v1/convert/source/async", json={"sources": [{"kind": "file", "filename": "s.pdf",
                                                                       "base64_string": base64.b64encode(PDF + b"%s").decode()}]},
                        headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200 and last_call(env.ctx)["tool_name"] == "convert_source_async"
    tid = r.json()["task_id"]
    assert env.client.get(f"/v1/status/poll/{tid}", params={"wait": 10}, headers={"X-Api-Key": raw}).json()["task_status"] == "success"


def test_poll_wait_is_capped_and_other_token_sees_404(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=4.0)
    env.ctx.config.compat.poll_max_wait_s = 1
    raw, _ = env.issue()
    other, _ = env.issue()
    tid = _submit(env, raw).json()["task_id"]
    t0 = time.time()
    st = env.client.get(f"/v1/status/poll/{tid}", params={"wait": 60}, headers={"X-Api-Key": raw}).json()
    assert time.time() - t0 < 3 and st["task_status"] in ("pending", "started")
    assert env.client.get(f"/v1/status/poll/{tid}", headers={"X-Api-Key": other}).status_code == 404
    assert env.client.get(f"/v1/result/{tid}", headers={"X-Api-Key": other}).status_code == 404
    assert env.client.get("/v1/status/poll/nope", headers={"X-Api-Key": raw}).json() == {"detail": "task not found"}


def test_async_failure_result(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="error")
    raw, _ = env.issue()
    tid = _submit(env, raw, name="bad.pdf", data=PDF + b"\n% bad\n").json()["task_id"]
    st = env.client.get(f"/v1/status/poll/{tid}", params={"wait": 10}, headers={"X-Api-Key": raw}).json()
    assert st["task_status"] == "failure" and st["error_message"].startswith("engine")
    assert st["failure"]["phase"] == "execution" and st["failure"]["category"] == "backend_failure"
    res = env.client.get(f"/v1/result/{tid}", headers={"X-Api-Key": raw}).json()
    # spike S2: docling's TaskFailureResult.failure is PublicFailureInfo (`message`, not the plan's `error_message`)
    assert res["kind"] == "TaskFailureResult" and "engine" in res["failure"]["message"]
    assert res["failure"]["details"]["error_kind"] == "engine"


def test_async_unsupported_file_is_an_immediate_failure(workers):
    env = workers
    raw, _ = env.issue()
    st = _submit(env, raw, name="x.exe", data=b"MZ" + b"\0" * 64).json()
    assert st["task_status"] == "failure" and st["error_message"].startswith("unsupported_file")
    res = env.client.get(f"/v1/result/{st['task_id']}", headers={"X-Api-Key": raw}).json()
    assert res["kind"] == "TaskFailureResult" and res["failure"]["category"] == "policy"


def test_async_cache_hit_and_cap_429(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    raw, _ = env.issue()
    first = _submit(env, raw).json()["task_id"]
    env.client.get(f"/v1/status/poll/{first}", params={"wait": 10}, headers={"X-Api-Key": raw})
    n_jobs = len(env.ctx.store.list_jobs())
    again = _submit(env, raw).json()
    assert again["task_status"] == "success" and again["task_id"] != first       # cache hit: immediately resolvable
    assert len(env.ctx.store.list_jobs()) == n_jobs
    assert env.client.get(f"/v1/result/{again['task_id']}", headers={"X-Api-Key": raw}).json()["document"]["doc4ai"]["cached"] is True
    env.ctx.config.compat.max_concurrent_jobs_per_token = 1
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=2.0)
    assert _submit(env, raw, name="s1.pdf", data=PDF + b"\n%1\n").status_code == 200
    r = _submit(env, raw, name="s2.pdf", data=PDF + b"\n%2\n")
    assert r.status_code == 429 and r.headers["retry-after"] == "5" and last_call(env.ctx)["error_code"] == "too_many_jobs"
    assert not list(env.ctx.uploads.dir.glob("compat-*.part"))


def test_async_same_file_running_returns_a_pollable_task(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=1.5)
    a, _ = env.issue()
    b, _ = env.issue()
    first = _submit(env, a, name="dup.pdf", data=PDF + b"\n% dup\n").json()["task_id"]
    second = _submit(env, b, name="dup.pdf", data=PDF + b"\n% dup\n").json()["task_id"]
    assert second != first                       # another token's job: an opaque id of its own
    st = env.client.get(f"/v1/status/poll/{second}", params={"wait": 10}, headers={"X-Api-Key": b}).json()
    assert st["task_status"] == "success"
    assert env.client.get(f"/v1/result/{second}", headers={"X-Api-Key": b}).json()["status"] == "success"
    assert len([j for j in env.ctx.store.list_jobs() if j["origin"] == "compat"]) == 1


def test_read_scope_is_enough_for_poll_and_result(workers):
    env = workers
    conv, _ = env.issue()
    reader, _ = env.issue(scopes=("doc4ai:read",))
    tid = _submit(env, conv).json()["task_id"]
    assert env.client.get(f"/v1/status/poll/{tid}", headers={"X-Api-Key": reader}).status_code == 404   # not its task
    assert env.client.post("/v1/convert/file/async", files={"files": ("a.pdf", io.BytesIO(PDF))}, headers={"X-Api-Key": reader}).status_code == 403
    env.client.get(f"/v1/status/poll/{tid}", params={"wait": 10}, headers={"X-Api-Key": conv})
    # a read-only token reaches its own tasks: none here, but the scope check passes (404, not 403)
    assert env.client.get("/v1/result/zzz", headers={"X-Api-Key": reader}).status_code == 404


def test_sync_timeout_then_poll_and_result(workers):
    """A sync request that ran out of time (504) is finished through the async endpoints (spec §4.3 step 6)."""
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=3.0)
    env.ctx.config.compat.sync_timeout_s = 1
    raw, _ = env.issue()
    body = _post_file(env, raw, name="slow.pdf", data=PDF + b"\n% slow\n").json()
    st = env.client.get(f"/v1/status/poll/{body['task_id']}", params={"wait": 15}, headers={"X-Api-Key": raw}).json()
    assert st["task_status"] == "success"
    assert env.client.get(f"/v1/result/{body['task_id']}", headers={"X-Api-Key": raw}).json()["status"] == "success"


def test_expired_task_record_is_404(workers):
    env = workers
    raw, _ = env.issue()
    tid = _submit(env, raw).json()["task_id"]
    env.client.get(f"/v1/status/poll/{tid}", params={"wait": 10}, headers={"X-Api-Key": raw})
    env.ctx.store.prune_compat_tasks(older_than_ts=time.time() + 1)
    r = env.client.get(f"/v1/result/{tid}", headers={"X-Api-Key": raw})
    assert r.status_code == 404 and r.json() == {"detail": "task not found"}


def test_low_quality_task_is_success_and_its_result_partial_success(workers):
    """Task status is about the task (clients such as Haystack stop polling only on success|failure); the result's
    conversion status carries partial_success (spec §4.3 step 5)."""
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="low")
    raw, _ = env.issue()
    tid = _submit(env, raw, name="low.pdf", data=PDF + b"\n% low\n").json()["task_id"]
    st = env.client.get(f"/v1/status/poll/{tid}", params={"wait": 10}, headers={"X-Api-Key": raw}).json()
    assert st["task_status"] == "success" and st["task_meta"]["num_partially_succeeded"] == 1
    assert env.client.get(f"/v1/result/{tid}", headers={"X-Api-Key": raw}).json()["status"] == "partial_success"


def test_attaching_to_another_tokens_job_uses_none_of_my_slots(workers):
    """Review finding 3: the attached job belongs to someone else; it must not count against my cap."""
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=3.0)
    env.ctx.config.compat.max_concurrent_jobs_per_token = 1
    a, _ = env.issue()
    b, _ = env.issue()
    _submit(env, a, name="shared.pdf", data=PDF + b"\n% shared\n")
    key = _submit(env, b, name="shared.pdf", data=PDF + b"\n% shared\n").json()["task_id"]
    env.client.get(f"/v1/status/poll/{key}", headers={"X-Api-Key": b})
    r = _submit(env, b, name="mine.pdf", data=PDF + b"\n% mine\n")
    assert r.status_code == 200, r.text
