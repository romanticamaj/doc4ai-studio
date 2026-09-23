from aidoc.config import load_config
from aidoc.engines.fake import FakeEngine
from aidoc.engines.registry import get_engines


def test_real_registry_names_and_availability(tmp_root, monkeypatch):
    monkeypatch.delenv("AIDOC_FAKE_ENGINES", raising=False)
    engines = get_engines(load_config())
    assert list(engines) == ["markitdown", "docling", "mineru"]
    assert not any(e.available() for e in engines.values())        # no envs under the tmp root


def test_fake_registry(tmp_root, monkeypatch):
    monkeypatch.setenv("AIDOC_FAKE_ENGINES", "1")
    engines = get_engines(load_config())
    assert all(isinstance(e, FakeEngine) and e.available() for e in engines.values())
    assert [e.name for e in engines.values()] == ["markitdown", "docling", "mineru"]
