import json
import time

from aidoc import paths


def test_system_shape(client, ctx, tmp_root):
    s = client.get("/api/system").json()
    assert set(s["engines"]) == {"markitdown", "docling", "mineru"}
    assert s["engines"]["docling"] == {"installed": False, "ready": False}
    assert s["queue"] == {"paused": False, "length": 0, "running_task_id": None}
    assert s["disk_free"] > 0 and s["output_dir"].endswith("out") and s["version"] == "0.1.0"
    assert "gpu" in s and "long_paths_enabled" in s and s["workspace"] == "default"
    (tmp_root / "envs" / "mineru" / ".venv" / "Scripts").mkdir(parents=True)
    paths.venv_python("mineru").parent.mkdir(parents=True, exist_ok=True)
    paths.venv_python("mineru").write_text("")
    paths.ready_marker("mineru").write_text(json.dumps({"engine": "mineru", "checked_at": 1.0, "cuda": True,
                                                         "arch_list": ["sm_120"]}))
    e = client.get("/api/system").json()["engines"]["mineru"]
    assert e["installed"] and e["ready"] and e["cuda"] is True and e["checked_at"] == 1.0


def test_gpu_query_never_raises(monkeypatch):
    from aidoc import gpu

    def boom():
        raise RuntimeError("no nvml")
    monkeypatch.setattr(gpu, "_nvml_query", boom)
    assert gpu.query() is None


def _wait_kinds(ctx, kind, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        kinds = [(e["kind"], e["payload"]) for e in ctx.store.events_since(0)]
        if any(k == kind for k, _ in kinds):
            return kinds
        time.sleep(0.05)
    raise AssertionError(f"no {kind} event")


def test_setup_endpoint_streams_logs(client, ctx, monkeypatch):
    def fake_setup(engine, cfg, log, run=None):
        log("syncing")
        log("done")
        return True
    monkeypatch.setattr("aidoc.server.setup_runner.setup_engines.setup", fake_setup)
    r = client.post("/api/system/setup/docling")
    assert r.status_code == 202 and r.json()["engine"] == "docling" and r.json()["setup_id"]
    kinds = _wait_kinds(ctx, "setup.done")
    assert [p["line"] for k, p in kinds if k == "setup.log"] == ["syncing", "done"]
    assert next(p for k, p in kinds if k == "setup.done") == {"engine": "docling", "ok": True}
    _wait_kinds(ctx, "system.updated")
    assert client.post("/api/system/setup/nope").status_code == 404


def test_setup_conflict_and_failure(client, ctx, monkeypatch):
    import threading
    gate = threading.Event()

    def slow_setup(engine, cfg, log, run=None):
        gate.wait(5)
        raise RuntimeError("uv exploded")
    monkeypatch.setattr("aidoc.server.setup_runner.setup_engines.setup", slow_setup)
    assert client.post("/api/system/setup/all").status_code == 202
    r = client.post("/api/system/setup/mineru")
    assert r.status_code == 409 and r.json()["error"] == "setup_running"
    gate.set()
    kinds = _wait_kinds(ctx, "setup.done")
    done = next(p for k, p in kinds if k == "setup.done")
    assert done["ok"] is False and "uv exploded" in done["error"] and done["engine"] == "all"


def test_settings_get_put(client, ctx, tmp_root):
    s = client.get("/api/settings").json()["settings"]
    assert s["general"]["lang"] == "cht" and s["server"]["token"] == ""
    r = client.put("/api/settings", json={"general": {"lang": "en", "work_retention_days": 3},
                                          "engines": {"mineru_tier": "standard"}})
    assert r.status_code == 200 and r.json()["settings"]["general"]["lang"] == "en"
    assert ctx.config.engines.mineru_tier == "standard" and ctx.config.general.work_retention_days == 3
    assert 'lang = "en"' in (tmp_root / "aidoc.toml").read_text(encoding="utf-8")
    assert client.get("/api/settings").json()["settings"]["general"]["lang"] == "en"
    assert client.put("/api/settings", json={"general": {"bogus": 1}}).status_code == 422
    assert client.put("/api/settings", json={"bogus": {"x": 1}}).status_code == 422
    assert client.put("/api/settings", json={"general": {"lang": "fr"}}).status_code == 422
    assert client.put("/api/settings", json={"general": {"work_retention_days": "7"}}).status_code == 422
    assert client.put("/api/settings", json={"general": {"enable_audio": 1}}).status_code == 422
    r = client.put("/api/settings", json={"server": {"token": "x"}})
    assert r.status_code == 403 and r.json()["error"] == "token_readonly"
    assert ctx.config.general.lang == "en"                            # rejected requests change nothing


def test_settings_token_masked(token_ctx):
    from fastapi.testclient import TestClient

    from aidoc.server.app import create_app
    with TestClient(create_app(token_ctx)) as c:
        s = c.get("/api/settings", headers={"Authorization": "Bearer s3cret"}).json()["settings"]
        assert s["server"]["token"] == "***"
