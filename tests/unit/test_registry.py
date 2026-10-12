from aidoc.config import load_config
from aidoc.engines.fake import FakeEngine
from aidoc.engines.registry import get_engines


def test_real_registry_names_and_availability(tmp_root, monkeypatch):
    monkeypatch.delenv("AIDOC_FAKE_ENGINES", raising=False)
    engines = get_engines(load_config())
    assert list(engines) == ["markitdown", "docling", "mineru", "vlm"]
    assert not any(e.available() for e in engines.values())        # no envs under the tmp root


def test_fake_registry(tmp_root, monkeypatch):
    monkeypatch.setenv("AIDOC_FAKE_ENGINES", "1")
    engines = get_engines(load_config())
    assert all(isinstance(e, FakeEngine) for e in engines.values())
    assert [e.name for e in engines.values()] == ["markitdown", "docling", "mineru", "vlm"]
    # the fake vlm follows the configuration like the real one: backend "none" (default) -> not available
    assert [e.available() for e in engines.values()] == [True, True, True, False]


def test_fake_vlm_available_when_configured(tmp_root, monkeypatch):
    monkeypatch.setenv("AIDOC_FAKE_ENGINES", "1")
    monkeypatch.setenv("AIDOC_VLM_API_KEY", "k")
    (tmp_root / "aidoc.toml").write_text(
        '[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "m"\nremote_enabled = true\n',
        encoding="utf-8")
    assert get_engines(load_config())["vlm"].available()
