from __future__ import annotations
from pathlib import Path
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """Isolated project root + data dir for a test."""
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    (root / "out").mkdir()
    monkeypatch.setenv("AIDOC_ROOT", str(root))
    monkeypatch.setenv("AIDOC_DATA", str(root / "data"))
    monkeypatch.delenv("AIDOC_CONFIG", raising=False)
    return root


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES
