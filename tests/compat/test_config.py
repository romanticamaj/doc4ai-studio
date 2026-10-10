from pathlib import Path


def test_compat_defaults_and_load(tmp_path):
    from aidoc.config import load_config
    p = tmp_path / "aidoc.toml"
    p.write_text('[compat]\nenabled = false\nsync_timeout_s = 120\njson_content = "doc4ai"\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.compat.enabled is False and cfg.compat.sync_timeout_s == 120 and cfg.compat.json_content == "doc4ai"
    d = cfg.compat
    assert (d.max_file_mb, d.max_source_mb, d.embed_images_max_mb) == (200, 50, 20)
    assert (d.max_concurrent_jobs_per_token, d.poll_max_wait_s, d.task_retention_days) == (8, 60, 30)
    assert "compat" in cfg.to_dict()


def test_committed_toml_has_compat():
    from aidoc.config import load_config
    cfg = load_config(Path(__file__).resolve().parents[2] / "aidoc.toml")
    assert cfg.compat.enabled is True and cfg.compat.json_content == "null"


def test_health_is_public_and_not_enveloped(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert r.headers["cache-control"] == "no-store"


def test_health_with_server_token_still_public(token_ctx):
    from fastapi.testclient import TestClient

    from aidoc.server.app import create_app
    with TestClient(create_app(token_ctx), client=("10.0.0.5", 5000), base_url="http://127.0.0.1:8765") as c:
        assert c.get("/health").status_code == 200
