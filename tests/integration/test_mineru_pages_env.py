"""Runs inside the MinerU env against the recorded fixture (spec 2026-10-01 §6.1)."""
import json
import os
import subprocess

import pytest

from doc4ai import paths
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


@pytest.mark.parametrize("doc", ["zh-kaohsiung-court-1973", "en-aashto-usrn-1984"])
def test_continued_blocks_no_longer_drain_whole_pages(doc, tmp_path):
    """T-002 root cause, on the real-sample regression set (spec 2026-10-22 §8.2: regression use only). MinerU's
    DEFAULT render plan put pages 2-3 and 7-8 of the 1973 court letter under pages 1 and 6, and the AASHTO
    report's page 5 table under page 4; every page with text now has its own section."""
    from doc4ai.engines.mineru import MineruEngine
    from doc4ai.models import ConvertOptions
    from doc4ai.pagemap import split_pages, squash
    from doc4ai.probe import probe_file
    from tests.manual import _required
    require_engine("mineru")
    pdf = paths.project_root() / "tests" / "fixtures" / "manual" / "realeval" / doc / "original.pdf"
    if not pdf.is_file():
        msg = f"regression sample {doc} not present"
        pytest.fail(msg) if _required() else pytest.skip(msg)
    r = MineruEngine().convert(pdf, tmp_path / "wd", ConvertOptions(output_dir=tmp_path / "o",
                                                                     lang="cht" if doc.startswith("zh") else "en"),
                               None, lambda f, line: None, timeout_s=1800, probe=probe_file(pdf))
    _, secs = split_pages(r.markdown)
    assert all(len(squash(secs.get(p, ""))) >= 20 for p in range(1, r.page_count + 1)), \
        {p: len(squash(secs.get(p, ""))) for p in range(1, r.page_count + 1)}
