"""The project was renamed aidoc -> doc4ai: everything an existing install relies on keeps working."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:          # Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[2]


def _env_after_import(extra: dict) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AIDOC_", "DOC4AI_"))}
    code = "import doc4ai, os; print(os.environ.get('DOC4AI_TOKEN'))"
    return subprocess.run([sys.executable, "-c", code], env={**env, **extra}, capture_output=True, text=True,
                          check=True).stdout.strip()


def test_old_environment_variables_still_work():
    assert _env_after_import({"AIDOC_TOKEN": "old"}) == "old"


def test_new_environment_variable_wins_over_the_old_one():
    assert _env_after_import({"AIDOC_TOKEN": "old", "DOC4AI_TOKEN": "new"}) == "new"


@pytest.mark.parametrize("files,want", [(["aidoc.toml"], "aidoc.toml"), (["doc4ai.toml"], "doc4ai.toml"),
                                        (["aidoc.toml", "doc4ai.toml"], "doc4ai.toml"), ([], "doc4ai.toml")])
def test_config_file_falls_back_to_the_old_name(tmp_path, monkeypatch, files, want):
    from doc4ai import config
    monkeypatch.delenv("DOC4AI_CONFIG", raising=False)
    monkeypatch.setenv("DOC4AI_ROOT", str(tmp_path))
    for f in files:
        (tmp_path / f).write_text("", encoding="utf-8")
    assert config.config_path().name == want


def test_both_commands_are_installed():
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["doc4ai"] == scripts["aidoc"] == "doc4ai.cli:main"


@pytest.mark.parametrize("exe", ["doc4ai.exe", "aidoc.exe", "doc4ai", "aidoc"])
def test_a_server_started_with_either_command_is_recognised(monkeypatch, exe):
    from doc4ai import lockfile

    class Proc:
        def __init__(self, pid):
            pass

        def cmdline(self):
            return [rf"C:\venv\Scripts\{exe}", "serve"]

    monkeypatch.setattr(lockfile.psutil, "Process", Proc)
    assert lockfile.is_doc4ai_process(os.getpid() + 1)


def test_persisted_names_did_not_move():
    """Existing data stays readable: the database file, its version column and the output format value."""
    from doc4ai import markers, store
    src = (ROOT / "src/doc4ai/server/app.py").read_text(encoding="utf-8")
    assert '"aidoc.db"' in src and "aidoc_version" in Path(store.__file__).read_text(encoding="utf-8")
    assert "aidoc" in markers.FORMATS
