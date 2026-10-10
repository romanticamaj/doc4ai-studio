"""Integration recipes (spec §8, plan Task 12): README section, one page per platform, the Kotaemon reference
loader in sync with its replayed request shape."""
import ast
import json
import py_compile
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INTEGRATIONS = ROOT / "docs" / "integrations"
RECIPES = {"open-webui": "A", "ragflow": "B", "kotaemon": "B", "dify": "C", "curl": "A"}


def test_readme_has_the_rag_section_with_the_rendered_env_block():
    from aidoc.mcp.snippets import openwebui_env
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## RAG platforms (docling-serve compatible API)" in readme
    assert openwebui_env("http://<host>:<port>/mcp") in readme              # same single source as the token dialog
    assert "DOCLING_SERVER_URL=http://<host>:<port>\n" in readme
    for name in RECIPES:
        assert f"docs/integrations/{name}.md" in readme
    assert readme.index("## MCP") < readme.index("## RAG platforms") < readme.index("## When to use something else")


@pytest.mark.parametrize("name", sorted(RECIPES))
def test_each_recipe_names_its_feasibility_grade_and_unverified_items(name):
    text = (INTEGRATIONS / f"{name}.md").read_text(encoding="utf-8")
    m = re.search(r"可行性[：:]\s*\*{0,2}([ABC?？])", text)
    assert m and m.group(1) == RECIPES[name], name
    assert "未驗證" in text, name


def _constants(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out


def test_kotaemon_loader_compiles_and_matches_its_shape(tmp_path):
    loader = INTEGRATIONS / "kotaemon_doc4ai_loader.py"
    py_compile.compile(str(loader), cfile=str(tmp_path / "loader.pyc"), doraise=True)
    c = _constants(loader)
    shape = json.loads((ROOT / "tests" / "compat" / "shapes" / "kotaemon_doc4ai_loader.json").read_text(encoding="utf-8"))
    assert c["CONVERT_PATH"] == shape["path"] and c["FILE_FIELD"] == shape["file_field"]
    assert c["FORM"] == shape["form"] and c["API_KEY_HEADER"] in shape["headers"]
    assert c["PAGE_BREAK"] == "\f"
