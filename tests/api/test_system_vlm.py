import json
import time

from tests.fakes.vlm_server import FakeVlmServer


def test_system_lists_vlm_with_backend_none(client):
    v = client.get("/api/system").json()["engines"]["vlm"]
    assert v["backend"] == "none" and v["installed"] is False and v["ready"] is False and v["roles"] == {"fallback": True, "repair": True}


def test_system_vlm_openai_fields_and_budget(client_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_VLM_API_KEY", "k")
    c = client_factory(toml='[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:1234/v1"\nmodel = "m"\nremote_enabled = true\nremote_daily_page_budget = 50\n')
    c.app.state.ctx.store.add_vlm_usage(time.strftime("%Y-%m-%d"), "openai", "127.0.0.1:1234", 7, 7, 100, 50)
    v = c.get("/api/system").json()["engines"]["vlm"]
    assert v["backend"] == "openai" and v["remote"] is True and v["local_endpoint"] is True and v["endpoint_host"] == "127.0.0.1:1234"
    assert v["budget"] == {"used_today": 7, "requests_today": 7, "limit": 50} and v["ready"] is True
    u = c.get("/api/system/vlm/usage?days=7").json()
    assert u["today"]["pages"] == 7 and u["days"][0]["prompt_tokens"] == 100


def test_vlm_check_against_fake_endpoint(client_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("AIDOC_VLM_API_KEY", "sk-test")
    with FakeVlmServer({"default": "ok", "pages": {"1": "AIDOC VLM CHECK 繁體中文 2026"}, "require_key": "sk-test"}) as base:
        c = client_factory(toml=f'[vlm]\nbackend = "openai"\nbase_url = "{base}"\nmodel = "fake-vlm"\nremote_enabled = true\n')
        r = c.post("/api/system/vlm/check"); assert r.status_code == 202
        done = c.wait_event("setup.done", lambda p: p["engine"] == "vlm", timeout=10)
        assert done["ok"] is True
        v = c.get("/api/system").json()["engines"]["vlm"]
        assert v["checked_at"] and v["ready"]
    with FakeVlmServer({"default": "unauthorized"}) as base:
        c = client_factory(toml=f'[vlm]\nbackend = "openai"\nbase_url = "{base}"\nmodel = "fake-vlm"\nremote_enabled = true\n')
        c.post("/api/system/vlm/check")
        done = c.wait_event("setup.done", lambda p: p["engine"] == "vlm", timeout=10)
        assert done["ok"] is False and "401" in done["error"]


def test_vlm_check_never_leaks_the_key(client_factory, monkeypatch):
    monkeypatch.setenv("AIDOC_VLM_API_KEY", "sk-test-hygiene")
    with FakeVlmServer({"default": "ok", "pages": {"1": "AIDOC VLM CHECK 繁體中文 2026"},
                        "require_key": "sk-test-hygiene"}) as base:
        c = client_factory(toml=f'[vlm]\nbackend = "openai"\nbase_url = "{base}"\nmodel = "fake-vlm"\nremote_enabled = true\n')
        assert c.post("/api/system/vlm/check").status_code == 202
        assert c.wait_event("setup.done", lambda p: p["engine"] == "vlm")["ok"] is True
    everything = json.dumps([e["payload"] for e in c.ctx.store.events_since(0)]) + c.get("/api/system").text
    assert "sk-test-hygiene" not in everything


def test_vlm_local_backend_reports_unsupported(client_factory):
    c = client_factory(toml='[vlm]\nbackend = "local"\n')
    v = c.get("/api/system").json()["engines"]["vlm"]
    assert v["local_supported"] is False and v["ready"] is False and v["backend"] == "local"
    c.post("/api/system/vlm/check")
    done = c.wait_event("setup.done", lambda p: p["engine"] == "vlm")
    assert done["ok"] is False and "not supported on this platform yet" in done["error"]
