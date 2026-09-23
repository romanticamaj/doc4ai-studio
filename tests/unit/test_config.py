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
