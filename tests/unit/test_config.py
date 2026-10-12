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
    p.write_text('[mcp]\nenabled = false\nallowed_hosts = ["box.tail74077f.ts.net"]\nlocal_path_roots = []\n'
                 'response_token_budget = 4000\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.mcp.enabled is False
    assert cfg.mcp.allowed_hosts == ["box.tail74077f.ts.net"]
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


def test_save_config_keeps_crlf_line_endings(tmp_path):
    """Item 14: saving Settings on Windows rewrote a CRLF aidoc.toml with LF (a whole-file diff)."""
    from aidoc.config import load_config, save_config
    p = tmp_path / "aidoc.toml"
    p.write_bytes(b"# my settings\r\n[general]\r\nlang = \"cht\"\r\n\r\n[mcp]\r\nrate_limit_per_min = 60\r\n")
    cfg = load_config(p)
    save_config(cfg, p, {"mcp": {"rate_limit_per_min": 90}})
    raw = p.read_bytes()
    assert b"rate_limit_per_min = 90\r\n" in raw and b"# my settings\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n")                            # no bare LF anywhere
    lf = tmp_path / "lf.toml"
    lf.write_bytes(b"[mcp]\nrate_limit_per_min = 60\n")
    save_config(load_config(lf), lf, {"mcp": {"rate_limit_per_min": 70}})
    assert b"\r" not in lf.read_bytes()


def _write_vlm(tmp_path, body):
    p = tmp_path / "aidoc.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_vlm_defaults_are_off(tmp_path):
    cfg = load_config(_write_vlm(tmp_path, ""))
    v = cfg.vlm
    assert v.backend == "none" and v.remote_enabled is False and v.use_as_fallback and v.use_for_repair
    assert v.api_key_env == "AIDOC_VLM_API_KEY" and v.remote_daily_page_budget == 500 and v.local_preset == "paddleocr-vl-1.6"


def test_vlm_openai_requires_clean_base_url(tmp_path):
    from aidoc.config import ConfigError
    with pytest.raises(ConfigError, match="base_url"):
        load_config(_write_vlm(tmp_path, '[vlm]\nbackend = "openai"\nbase_url = "http://user:key@host/v1"\nmodel = "m"\n'))
    with pytest.raises(ConfigError, match="base_url"):
        load_config(_write_vlm(tmp_path, '[vlm]\nbackend = "openai"\nbase_url = "host/v1"\nmodel = "m"\n'))
    with pytest.raises(ConfigError, match="model"):
        load_config(_write_vlm(tmp_path, '[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:1234/v1"\n'))


def test_vlm_unknown_backend_is_fatal(tmp_path):
    from aidoc.config import ConfigError
    with pytest.raises(ConfigError, match="backend"):
        load_config(_write_vlm(tmp_path, '[vlm]\nbackend = "paddle"\n'))


def test_vlm_remote_enabled_without_openai_warns(tmp_path):
    cfg = load_config(_write_vlm(tmp_path, '[vlm]\nbackend = "none"\nremote_enabled = true\n'))
    assert cfg.vlm.remote_enabled is False and any("remote_enabled" in w for w in cfg.warnings)


def test_vlm_inline_key_warns(tmp_path):
    cfg = load_config(_write_vlm(tmp_path, '[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:1/v1"\nmodel = "m"\napi_key = "sk-x"\n'))
    assert any("AIDOC_VLM_API_KEY" in w for w in cfg.warnings)


def test_vlm_ranges_are_checked(tmp_path):
    from aidoc.config import ConfigError
    with pytest.raises(ConfigError, match="max_concurrent"):
        load_config(_write_vlm(tmp_path, '[vlm]\nmax_concurrent = 9\n'))
