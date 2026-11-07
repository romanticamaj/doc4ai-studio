from doc4ai import paths
from doc4ai.engines.host import runner_env


def test_envs_and_models_overrides(tmp_root, tmp_path, monkeypatch):
    monkeypatch.setenv("DOC4AI_ENVS", str(tmp_path / "E"))
    monkeypatch.setenv("DOC4AI_MODELS", str(tmp_path / "M"))
    assert paths.envs_dir() == tmp_path / "E" and paths.models_dir() == tmp_path / "M"
    assert paths.venv_python("docling").is_relative_to(tmp_path / "E")
    assert runner_env(tmp_root / "data")["DOC4AI_MODELS_DIR"] == str(tmp_path / "M")
