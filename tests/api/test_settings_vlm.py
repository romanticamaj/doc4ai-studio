def test_settings_mask_and_validate_vlm(client_factory):
    c = client_factory(toml='[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:1/v1"\nmodel = "m"\napi_key = "sk-secret"\n')
    s = c.get("/api/settings").json()["settings"]
    assert s["vlm"]["api_key"] == "***" and "sk-secret" not in c.get("/api/settings").text
    assert c.put("/api/settings", json={"vlm": {"api_key": "***", "model": "m2"}}).status_code == 200
    assert c.app.state.ctx.config.vlm.api_key == "sk-secret" and c.app.state.ctx.config.vlm.model == "m2"
    assert c.put("/api/settings", json={"vlm": {"backend": "none", "remote_enabled": True}}).status_code == 422
    assert c.put("/api/settings", json={"vlm": {"base_url": "http://u:p@h/v1"}}).status_code == 422
    assert c.put("/api/settings", json={"vlm": {"max_concurrent": 9}}).status_code == 422
    assert c.put("/api/settings", json={"vlm": {"api_key": "sk-new"}}).status_code == 200
    assert c.app.state.ctx.config.vlm.api_key == "sk-new"


def test_changing_the_endpoint_host_withdraws_consent(client_factory, monkeypatch):
    c = client_factory(toml='[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:1/v1"\nmodel = "m"\nremote_enabled = true\n')
    assert c.put("/api/settings", json={"vlm": {"base_url": "https://api.example.com/v1"}}).status_code == 200
    assert c.app.state.ctx.config.vlm.remote_enabled is False
    r = c.put("/api/settings", json={"vlm": {"base_url": "https://api.other.com/v1", "remote_enabled": True}})
    assert r.status_code == 200 and c.app.state.ctx.config.vlm.remote_enabled is True
    assert c.put("/api/settings", json={"vlm": {"base_url": "https://api.other.com/v2"}}).status_code == 200
    assert c.app.state.ctx.config.vlm.remote_enabled is True            # same host: consent stands


def test_local_backend_is_refused_while_unsupported(client_factory):
    c = client_factory()
    r = c.put("/api/settings", json={"vlm": {"backend": "local"}})
    assert r.status_code == 422 and "not supported on this platform yet" in r.json()["detail"]
