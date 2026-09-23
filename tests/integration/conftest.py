"""Slow tests use the real engine envs and downloaded models, but an isolated root/data dir (DB, output)."""
from __future__ import annotations

import pytest

from aidoc import paths as _paths

REPO_ROOT = _paths.project_root()


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    (root / "out").mkdir()
    monkeypatch.setenv("AIDOC_ROOT", str(root))
    monkeypatch.setenv("AIDOC_DATA", str(root / "data"))
    monkeypatch.setenv("AIDOC_ENVS", str(REPO_ROOT / "envs"))
    monkeypatch.setenv("AIDOC_MODELS", str(REPO_ROOT / "data" / "models"))
    monkeypatch.delenv("AIDOC_CONFIG", raising=False)
    monkeypatch.delenv("AIDOC_FAKE_ENGINES", raising=False)
    # use the repo's aidoc.toml (spike decisions live there)
    monkeypatch.setenv("AIDOC_CONFIG", str(REPO_ROOT / "aidoc.toml"))
    return root
