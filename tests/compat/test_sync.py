import io
import json
import threading
import time

from tests.compat.conftest import last_call

PDF = (b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
       b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
       b"trailer<</Root 1 0 R>>\n%%EOF\n")


def _post_file(env, raw, name="a.pdf", data=PDF, headers=None, **form):
    return env.client.post("/v1/convert/file", files={"files": (name, io.BytesIO(data), "application/pdf")},
                           data=form, headers={"X-Api-Key": raw, **(headers or {})})


def test_openwebui_shape_sync_success(workers):
    env = workers
    raw, tid = env.issue()
    r = _post_file(env, raw, image_export_mode="placeholder", md_page_break_placeholder="\f")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success" and body["errors"] == [] and body["document"]["filename"] == "a.pdf"
    md = body["document"]["md_content"]
    pages = body["document"]["doc4ai"]["pages"]
    assert pages >= 1 and md.count("\f") == pages - 1 and "<!-- page:" not in md
    assert body["document"]["json_content"] is None and "workspace" not in body
    assert body["doc4ai"]["doc_id"] == body["document"]["doc4ai"]["doc_id"]
    job = env.ctx.store.get_job(body["document"]["doc4ai"]["job_id"])
    assert job["origin"] == "compat"
    row = last_call(env.ctx)
    assert row["tool_name"] == "convert_file" and row["status"] == "ok" and row["job_id"] == job["id"]
    assert row["method"] == "compat/docling" and row["response_tokens_est"] > 0
    args = json.loads(row["args_summary"])
    assert args["filename"] == "a.pdf" and args["placeholder"] is True and "content" not in args
    assert env.ctx.store.get_compat_task(job["id"])["token_id"] == tid


def test_images_become_placeholders_in_real_output(workers):
    """The fake engine emits one image per page: placeholder mode (Open WebUI's) replaces every one."""
    env = workers
    raw, _ = env.issue()
    md = _post_file(env, raw, name="img.pdf", data=PDF + b"\n% img\n").json()["document"]["md_content"]
    assert "<!-- image -->" in md and "assets/" not in md


def test_openwebui_uuid_prefixed_path_is_reduced_to_the_name(workers):
    env = workers
    raw, _ = env.issue()
    name = "/app/backend/data/uploads/3f2a9c4e-1b2d-4c5e-8f90-a1b2c3d4e5f6_report.pdf"
    body = _post_file(env, raw, name=name, data=PDF + b"\n% uuid\n").json()
    assert body["document"]["filename"] == "report.pdf"
    doc = env.ctx.store.get_document(body["doc4ai"]["doc_id"])
    assert doc["source_path"] == "report.pdf"


def test_source_shape_and_v1alpha(workers):
    import base64
    env = workers
    raw, _ = env.issue()
    b64 = base64.b64encode(PDF).decode()
    r = env.client.post("/v1/convert/source", json={"options": {"to_formats": ["json", "md", "text"], "do_chunking": True},
                                                    "sources": [{"kind": "file", "filename": "s.pdf", "base64_string": b64}]},
                        headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200, r.text
    d = r.json()["document"]
    assert d["md_content"] and d["text_content"] and d["json_content"] is None
    assert d["doc4ai"]["unsupported_formats"] == ["json"] and "do_chunking" in d["doc4ai"]["ignored_options"]
    assert d["doc4ai"]["cached"] is False                     # a new file (the plan's `is True` was a typo, its comment says so)
    assert last_call(env.ctx)["tool_name"] == "convert_source"
    r2 = env.client.post("/v1alpha/convert/source", json={"file_sources": [{"filename": "s.pdf", "base64_string": b64}]},
                         headers={"Authorization": f"Bearer {raw}"})
    assert r2.status_code == 200 and r2.json()["document"]["doc4ai"]["cached"] is True
    assert last_call(env.ctx)["protocol_version"] == "docling-serve-v1alpha"


def test_cache_hit_makes_no_job(workers):
    env = workers
    raw, _ = env.issue()
    first = _post_file(env, raw).json()
    n_jobs = len(env.ctx.store.list_jobs())
    second = _post_file(env, raw).json()
    assert second["document"]["doc4ai"]["cached"] is True and len(env.ctx.store.list_jobs()) == n_jobs
    assert second["document"]["doc4ai"]["doc_id"] == first["document"]["doc4ai"]["doc_id"]
    third = _post_file(env, raw, name="renamed.pdf").json()        # the same bytes under another name: still a hit
    assert third["document"]["doc4ai"]["cached"] is True and third["document"]["filename"] == "renamed.pdf"
    assert _post_file(env, raw, doc4ai_force="true").json()["document"]["doc4ai"]["cached"] is False


def test_unsupported_file_and_engine_failure_are_status_failure(workers):
    env = workers
    raw, _ = env.issue()
    r = _post_file(env, raw, name="x.exe", data=b"MZ" + b"\0" * 64)
    assert r.status_code == 200 and r.json()["status"] == "failure"
    assert r.json()["errors"][0]["error_message"].startswith("unsupported_file")
    assert r.json()["errors"][0]["component_type"] == "user_input"
    assert last_call(env.ctx)["status"] == "tool_error" and last_call(env.ctx)["error_code"] == "unsupported_file"
    from tests.fakes.scenario import write_scenario
    write_scenario(env.ctx.config.root / "sc.json", default="error")     # every engine fails
    r = _post_file(env, raw, name="fail.pdf", data=PDF + b"\n% fail\n")
    assert r.json()["status"] == "failure" and "engine" in r.json()["errors"][0]["error_message"]
    assert r.json()["document"]["doc4ai"]["tried"]
    assert last_call(env.ctx)["error_code"] == "conversion_failed"


def test_low_quality_is_partial_success(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="low")
    raw, _ = env.issue()
    body = _post_file(env, raw, name="low.pdf", data=PDF + b"\n% low\n").json()
    assert body["status"] == "partial_success" and body["errors"][0]["module_name"] == "doc4ai.quality"


def test_multiple_files_422_and_wrong_source_kind_422(compat_env):
    raw, _ = compat_env.issue()
    r = compat_env.client.post("/v1/convert/file", files=[("files", ("a.pdf", PDF)), ("files", ("b.pdf", PDF))], headers={"X-Api-Key": raw})
    assert r.status_code == 422 and r.json()["detail"] == "one file per request"
    r = compat_env.client.post("/v1/convert/source", json={"sources": [{"kind": "http", "url": "http://x"}]}, headers={"X-Api-Key": raw})
    assert r.status_code == 422 and r.json()["detail"].startswith("unsupported_source")
    r = compat_env.client.post("/v1/convert/source", json={"sources": []}, headers={"X-Api-Key": raw})
    assert r.status_code == 422
    r = compat_env.client.post("/v1/convert/source", json={"sources": [{"kind": "file", "filename": "a.pdf", "base64_string": "!!"}]},
                               headers={"X-Api-Key": raw})
    assert r.status_code == 422 and "base64" in json.dumps(r.json()["detail"])
    r = compat_env.client.post("/v1/convert/source", json={"target": {"kind": "zip"}, "sources": [{"kind": "file", "filename": "a.pdf",
                                                                                                "base64_string": "JVBERg=="}]},
                               headers={"X-Api-Key": raw})
    assert r.status_code == 422 and r.json()["detail"] == "unsupported_target: zip"
    r = compat_env.client.post("/v1/convert/file", data={"to_formats": "md"}, headers={"X-Api-Key": raw})
    assert r.status_code == 422
    r = compat_env.client.post("/v1/convert/file", files={"files": ("a.pdf", PDF)}, data={"target_type": "zip"},
                               headers={"X-Api-Key": raw})
    assert r.status_code == 422 and r.json()["detail"] == "unsupported_target: zip"
    assert not list(compat_env.ctx.uploads.dir.glob("*.part"))


def test_oversized_stream_is_cut_and_part_deleted(compat_env):
    env = compat_env
    env.ctx.config.compat.max_file_mb = 1
    raw, _ = env.issue()
    big = PDF + b"\0" * (2 * 1024 * 1024)
    r = _post_file(env, raw, data=big)
    assert r.status_code == 413 and "1 MB" in r.json()["detail"]
    assert not list((env.ctx.uploads.dir).glob("*.part"))
    assert last_call(env.ctx)["error_code"] == "file_too_large"


def test_oversized_source_body_is_413(compat_env):
    env = compat_env
    env.ctx.config.compat.max_source_mb = 1
    raw, _ = env.issue()
    r = env.client.post("/v1/convert/source", content=b'{"sources": [' + b" " * (2 * 1024 * 1024) + b"]}",
                        headers={"X-Api-Key": raw, "Content-Type": "application/json"})
    assert r.status_code == 413 and last_call(env.ctx)["error_code"] == "file_too_large"


def test_sync_timeout_504_then_async_result(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=3.0)
    env.ctx.config.compat.sync_timeout_s = 1          # below the settings minimum (10): a test-only shortcut
    raw, _ = env.issue()
    r = _post_file(env, raw, name="slow.pdf", data=PDF + b"\n% slow\n")
    assert r.status_code == 504
    body = r.json()
    assert body["task_id"] and body["status_url"] == f"/v1/status/poll/{body['task_id']}"
    assert body["result_url"] == f"/v1/result/{body['task_id']}"
    assert last_call(env.ctx)["error_code"] == "sync_timeout"
    deadline = time.time() + 15                        # the job was not cancelled: it finishes on its own
    while time.time() < deadline and env.ctx.store.get_job(body["task_id"])["status"] != "done":
        time.sleep(0.3)
    assert env.ctx.store.get_job(body["task_id"])["status"] == "done"
    # polling / result after a 504: tests/compat/test_async.py::test_sync_timeout_then_poll_and_result


def test_concurrent_same_file_waits_on_the_running_task(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=1.5)
    raw, _ = env.issue()
    results = {}

    def go(k):
        results[k] = _post_file(env, raw, name="same.pdf", data=PDF + b"\n% same\n").json()
    ts = [threading.Thread(target=go, args=(k,)) for k in range(2)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert {r["status"] for r in results.values()} == {"success"}
    assert len([j for j in env.ctx.store.list_jobs() if j["origin"] == "compat"]) == 1
    assert sorted(r["document"]["doc4ai"].get("attached", False) for r in results.values()) == [False, True]


def test_sync_waits_for_a_job_slot_instead_of_failing(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    env.ctx.config.compat.max_concurrent_jobs_per_token = 1
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=1.0)
    raw, _ = env.issue()
    results = []

    def go(n):
        results.append(_post_file(env, raw, name=f"f{n}.pdf", data=PDF + b"\n% " + str(n).encode()).status_code)
    ts = [threading.Thread(target=go, args=(n,)) for n in range(2)]
    [t.start() for t in ts]
    [t.join(40) for t in ts]
    assert results == [200, 200]


def test_revocation_mid_wait_does_not_abort_the_running_job(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=1.5)
    raw, tid = env.issue()
    out = {}
    t = threading.Thread(target=lambda: out.update(r=_post_file(env, raw, name="rev.pdf", data=PDF + b"\n% rev\n")))
    t.start()
    time.sleep(0.4)
    env.ctx.store.revoke_api_token(tid, "mid", at=time.time())
    t.join(30)
    assert out["r"].status_code == 200 and out["r"].json()["status"] == "success"
    row = next(c for c in env.ctx.store.list_mcp_calls(limit=10) if c["tool_name"] == "convert_file")
    assert row["token_id"] == tid and row["status"] == "ok"           # plan review focus 5: attributed
    assert _post_file(env, raw).status_code == 401


def test_rate_limit_applies_to_convert(workers):
    env = workers
    raw, tid = env.issue()
    env.ctx.store.update_api_token(tid, rate_limit_per_min=1)
    assert _post_file(env, raw).status_code == 200
    r = _post_file(env, raw)
    assert r.status_code == 429 and r.headers["retry-after"] and r.json()["detail"] == "rate limited"
    assert last_call(env.ctx)["status"] == "rate_limited"


def test_server_token_is_not_a_pat_on_v1(token_ctx):
    from fastapi.testclient import TestClient

    from aidoc.server.app import create_app
    with TestClient(create_app(token_ctx), client=("10.0.0.9", 5000), base_url="http://127.0.0.1:8765") as c:
        r = c.post("/v1/convert/file", headers={"Authorization": "Bearer s3cret"}, files={"files": ("a.pdf", PDF)})
        assert r.status_code == 401 and "detail" in r.json() and "workspace" not in r.json()


def test_an_explicit_engine_is_not_answered_by_another_engines_cache(workers):
    """Review finding 6: retrying with force_ocr / doc4ai_engine must not return the earlier engine's result."""
    env = workers
    raw, _ = env.issue()
    first = _post_file(env, raw, name="eng.pdf", data=PDF + b"\n% eng\n").json()["document"]["doc4ai"]
    other = next(e for e in ("markitdown", "docling", "mineru") if e != first["engine"])
    again = _post_file(env, raw, name="eng.pdf", data=PDF + b"\n% eng\n", doc4ai_engine=first["engine"]).json()
    assert again["document"]["doc4ai"]["cached"] is True
    redo = _post_file(env, raw, name="eng.pdf", data=PDF + b"\n% eng\n", doc4ai_engine=other).json()
    assert redo["document"]["doc4ai"]["cached"] is False and redo["document"]["doc4ai"]["engine"] == other
