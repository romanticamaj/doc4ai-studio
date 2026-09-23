import sys

from aidoc import paths


def test_root_from_env(tmp_root):
    assert paths.project_root() == tmp_root
    assert paths.data_dir() == tmp_root / "data"
    assert paths.models_dir() == tmp_root / "data" / "models"


def test_venv_python_platform(tmp_root):
    p = paths.venv_python("docling")
    if sys.platform == "win32":
        assert p == tmp_root / "envs" / "docling" / ".venv" / "Scripts" / "python.exe"
    else:
        assert p == tmp_root / "envs" / "docling" / ".venv" / "bin" / "python"


def test_runner_script_exists_in_package():
    assert paths.runner_dir().is_dir()
    assert paths.runner_script("docling").name == "docling_runner.py"
