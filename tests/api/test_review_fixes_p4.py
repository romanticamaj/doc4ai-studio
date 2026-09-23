"""P3 final-review minors fixed at the start of P4 (each failed first)."""
import logging

from tests.api.test_jobs_inputs import upload


def test_duplicate_upload_id_in_one_request_is_400(client, ctx, fixtures):
    uid = upload(client, fixtures / "text.pdf")
    r = client.post("/api/jobs", json={"inputs": [{"upload_id": uid}, {"upload_id": uid}]})
    assert r.status_code == 400
    assert r.json()["error"] == "duplicate_input" and r.json()["upload_id"] == uid
    assert ctx.store.list_jobs() == []                                  # nothing half-created
    assert ctx.store.get_upload(uid)["status"] == "complete"            # still usable


def test_cancel_of_a_finished_job_is_a_no_op(client, ctx, fixtures):
    job = client.post("/api/jobs", json={"inputs": [{"path": str(fixtures / "text.pdf")}]}).json()["job"]
    ctx.queue.process_next()
    assert client.get(f"/api/jobs/{job['id']}").json()["job"]["status"] == "done"
    r = client.post(f"/api/jobs/{job['id']}/cancel")
    assert r.status_code == 200 and r.json()["job"]["status"] == "done"
    assert ctx.store.get_job(job["id"])["status"] == "done"


def _pretend_running(client, ctx, fixtures, engine):
    job = client.post("/api/jobs", json={"inputs": [{"path": str(fixtures / "text.pdf")}]}).json()["job"]
    tid = ctx.store.list_tasks(job["id"])[0]["id"]
    ctx.store.update_task(tid, status="converting", engine=engine)
    ctx.queue.running_task_id = tid
    return tid


def test_setup_refused_while_that_engine_converts(client, ctx, fixtures, monkeypatch):
    calls = []
    monkeypatch.setattr("aidoc.server.setup_runner.setup_engines.setup", lambda e, c, log, run=None: calls.append(e))
    _pretend_running(client, ctx, fixtures, "docling")
    for eng in ("docling", "all"):
        r = client.post(f"/api/system/setup/{eng}")
        assert r.status_code == 409, eng
        assert r.json()["error"] == "engine_busy" and r.json()["engine"] == "docling"
    assert client.post("/api/system/setup/markitdown").status_code == 202   # another engine is fine
    ctx.queue.running_task_id = None


def test_setup_refused_while_a_task_is_still_probing(client, ctx, fixtures, monkeypatch):
    monkeypatch.setattr("aidoc.server.setup_runner.setup_engines.setup", lambda e, c, log, run=None: True)
    _pretend_running(client, ctx, fixtures, None)                       # engine not chosen yet: could be any
    r = client.post("/api/system/setup/mineru")
    assert r.status_code == 409 and r.json()["error"] == "engine_busy"
    ctx.queue.running_task_id = None


def test_access_log_redacts_query_token(caplog):
    from aidoc.server.logfilter import install_access_log_redaction
    install_access_log_redaction()
    logger = logging.getLogger("uvicorn.access")
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        logger.propagate = True
        try:
            logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5", "GET",
                        "/api/events?last_event_id=3&token=s3cret&x=1", "1.1", 200)
        finally:
            logger.propagate = False
    text = caplog.text
    assert "s3cret" not in text and "token=***" in text and "last_event_id=3" in text and "x=1" in text


def test_saving_settings_keeps_comments(client, ctx, tmp_root):
    toml = tmp_root / "aidoc.toml"
    toml.write_text('[general]\n# where results go\noutput_dir = "out"  # relative\nlang = "cht"\n',
                    encoding="utf-8")
    from aidoc.config import load_config
    ctx.config = load_config()
    assert client.put("/api/settings", json={"general": {"lang": "en"}}).status_code == 200
    text = toml.read_text(encoding="utf-8")
    assert "# where results go" in text and "# relative" in text and 'lang = "en"' in text
