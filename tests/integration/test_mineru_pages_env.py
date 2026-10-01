"""Runs inside the MinerU env against the recorded fixture (spec 2026-10-01 §6.1)."""
import json
import os
import subprocess

import pytest

from aidoc import paths
from tests.manual import require_engine

pytestmark = pytest.mark.slow

SCRIPT = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import mineru_runner as mr
from mineru.parser import ParseResult
d = sys.argv[2]
ref = open(d + '/markdown.md', encoding='utf-8').read()
pr = ParseResult.from_json(open(d + '/middle_json.json', encoding='utf-8').read())
pages, method = mr.render_pages(pr.middle_json, reference=ref)
print(json.dumps({"method": method, "pages": pages, "md": mr.assemble(pages), "ref": ref}))
"""


def test_render_plan_matches_markdown_md_on_recorded_output(fixtures):
    require_engine("mineru")
    env = {**os.environ, "PYTHONUTF8": "1", "MINERU_HOME": str(paths.models_dir() / "mineru")}
    out = subprocess.run([str(paths.venv_python("mineru")), "-c", SCRIPT, str(paths.runner_dir()),
                          str(fixtures / "mineru_recorded" / "furniture8")],
                         capture_output=True, text=True, encoding="utf-8", env=env, check=True)
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["method"] == "mineru_render_plan" and len(r["pages"]) == 8 and r["pages"][3] == ""
    assert r["md"].count("<!-- page: ") == 8 and "<!-- page: 4 -->\n\n<!-- page: 5 -->" in r["md"]
    assert "\n\n".join(p for p in r["pages"] if p) == r["ref"]
