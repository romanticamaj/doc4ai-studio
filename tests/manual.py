"""Real-sample gate (spec 2026-10-01 §10.2). Tests get real samples and engine envs only through these helpers:
missing ones skip normally and FAIL when AIDOC_REQUIRE_MANUAL=1 (acceptance mode)."""
import json
import os
from pathlib import Path

import pytest

from aidoc import paths

FIXTURES = Path(__file__).parent / "fixtures"
MANIFEST = FIXTURES / "manual_samples.json"


def _required() -> bool:
    return os.environ.get("AIDOC_REQUIRE_MANUAL") == "1"


def _manual_dir() -> Path:
    return Path(os.environ.get("AIDOC_MANUAL_DIR") or FIXTURES / "manual")


def require_engine(name: str) -> None:
    if paths.ready_marker(name).is_file() and paths.venv_python(name).is_file():
        return
    msg = f"engine env not ready: {name} (run: aidoc setup {name})"
    pytest.fail(msg) if _required() else pytest.skip(msg)


def manual_sample(name: str) -> Path:
    known = {s["name"] for s in json.loads(MANIFEST.read_text(encoding="utf-8"))}
    if name not in known:
        pytest.fail(f"{name} is not listed in {MANIFEST.name}")
    p = _manual_dir() / name
    if p.is_file():
        return p
    msg = f"manual sample missing: {name} — run: uv run python tests/fixtures/make_manual.py"
    pytest.fail(msg) if _required() else pytest.skip(msg)
