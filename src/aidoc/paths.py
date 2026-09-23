from __future__ import annotations
import os
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent            # src/aidoc


def project_root() -> Path:
    """AIDOC_ROOT env, else the checkout that contains this package (src/aidoc -> repo)."""
    env = os.environ.get("AIDOC_ROOT")
    if env:
        return Path(env).resolve()
    return _PKG_ROOT.parent.parent


def data_dir() -> Path:
    env = os.environ.get("AIDOC_DATA")
    return Path(env).resolve() if env else project_root() / "data"


def envs_dir() -> Path:
    return project_root() / "envs"


def models_dir() -> Path:
    return data_dir() / "models"


def venv_dir(engine: str) -> Path:
    return envs_dir() / engine / ".venv"


def venv_python(engine: str) -> Path:
    venv = venv_dir(engine)
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def ready_marker(engine: str) -> Path:
    return envs_dir() / engine / ".ready"


def runner_dir() -> Path:
    return _PKG_ROOT / "engines" / "runner"


def runner_script(engine: str) -> Path:
    return runner_dir() / f"{engine}_runner.py"
