import json

import pytest

from doc4ai import paths

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("engine", ["markitdown", "docling", "mineru"])
def test_env_ready(engine):
    assert paths.venv_python(engine).exists(), f"run: uv run doc4ai setup {engine}"
    ready = json.loads(paths.ready_marker(engine).read_text())
    if engine != "markitdown":
        assert ready["cuda"] is True and "sm_120" in ready["arch_list"]
