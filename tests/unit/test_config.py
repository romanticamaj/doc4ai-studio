import pytest

from aidoc.config import load_config, save_config


def test_defaults_when_missing(tmp_root):
    cfg = load_config()
    assert cfg.general.output_dir == "out" and cfg.general.lang == "cht"
    assert cfg.engines.mineru_tier == "basic" and cfg.limits.timeout_per_page_s == 60
    assert cfg.server.host == "127.0.0.1" and cfg.server.token == ""
    assert cfg.output_root() == tmp_root / "out"


def test_partial_file_overrides(tmp_root):
    (tmp_root / "aidoc.toml").write_text('[general]\nlang = "en"\n[engines]\nmineru_tier = "standard"\n', encoding="utf-8")
    cfg = load_config()
    assert cfg.general.lang == "en" and cfg.engines.mineru_tier == "standard"
    assert cfg.limits.timeout_min_s == 120


def test_env_override_path(tmp_root, tmp_path, monkeypatch):
    p = tmp_path / "other.toml"
    p.write_text('[server]\nport = 9999\n', encoding="utf-8")
    monkeypatch.setenv("AIDOC_CONFIG", str(p))
    assert load_config().server.port == 9999


def test_save_roundtrip(tmp_root):
    cfg = load_config()
    cfg.general.work_retention_days = 3
    save_config(cfg, tmp_root / "aidoc.toml")
    assert load_config().general.work_retention_days == 3


def test_unknown_key_rejected(tmp_root):
    (tmp_root / "aidoc.toml").write_text('[general]\nbogus = 1\n', encoding="utf-8")
    with pytest.raises(ValueError, match="bogus"):
        load_config()


def test_mcp_section_defaults_and_load(tmp_path, monkeypatch):
    from aidoc.config import load_config
    p = tmp_path / "aidoc.toml"
    p.write_text('[mcp]\nenabled = false\nallowed_hosts = ["box.tail0.ts.net"]\nlocal_path_roots = []\n'
                 'response_token_budget = 4000\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.mcp.enabled is False
    assert cfg.mcp.allowed_hosts == ["box.tail0.ts.net"]
    assert cfg.mcp.local_path_roots == []
    assert cfg.mcp.response_token_budget == 4000
    assert cfg.mcp.default_token_ttl_days == 90 and cfg.mcp.max_token_ttl_days == 365
    assert cfg.mcp.allow_no_expiry is False and cfg.mcp.rate_limit_per_min == 60
    assert cfg.mcp.max_concurrent_jobs_per_token == 3 and cfg.mcp.max_upload_mb == 20
    assert cfg.mcp.call_log_retention_days == 30 and cfg.mcp.call_log_max_rows == 200000
    assert "mcp" in cfg.to_dict() and cfg.to_dict()["mcp"]["enabled"] is False


def test_committed_aidoc_toml_has_mcp_section():
    from pathlib import Path

    from aidoc.config import load_config
    cfg = load_config(Path(__file__).resolve().parents[2] / "aidoc.toml")
    assert cfg.mcp.enabled is True and cfg.mcp.local_path_roots == []
