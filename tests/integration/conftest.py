"""Slow tests use the real engine envs and downloaded models, but an isolated root/data dir (DB, output)."""
from __future__ import annotations

import pytest

from doc4ai import paths as _paths

REPO_ROOT = _paths.project_root()


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    (root / "out").mkdir()
    monkeypatch.setenv("DOC4AI_ROOT", str(root))
    monkeypatch.setenv("DOC4AI_DATA", str(root / "data"))
    monkeypatch.setenv("DOC4AI_ENVS", str(REPO_ROOT / "envs"))
    monkeypatch.setenv("DOC4AI_MODELS", str(REPO_ROOT / "data" / "models"))
    monkeypatch.delenv("DOC4AI_CONFIG", raising=False)
    monkeypatch.delenv("DOC4AI_FAKE_ENGINES", raising=False)
    # use the repo's doc4ai.toml (spike decisions live there)
    monkeypatch.setenv("DOC4AI_CONFIG", str(REPO_ROOT / "doc4ai.toml"))
    # ... but never its Doc4AI folder: a live root_dir (e.g. D:\doc4ai) must not receive test copies
    # (spec 2026-10-29 §12.4); the env override beats general.root_dir
    monkeypatch.setenv("DOC4AI_LIBRARY_ROOT", str(root))
    return root
