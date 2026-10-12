"""Record third-party *native* Markdown (no Doc4AI runner, no normalize) of paged_furniture.pdf for the verify
dialect tests (spec 2026-10-05 verify §6): Docling `export_to_markdown(page_break_placeholder=...)` and MarkItDown
`convert()`. Runs each engine env's own Python. A missing recording is written; an existing one is compared with the
fresh one (engine drift shows up here, not as a silent change)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aidoc import paths
from aidoc.verify import verify_markdown
from tests.manual import require_engine

pytestmark = pytest.mark.slow

FIX = Path(__file__).resolve().parents[1] / "fixtures"
PDF = FIX / "paged_furniture.pdf"

DOCLING = r"""
import sys
sys.path.insert(0, sys.argv[1])
from docling_runner import build_converter
conv = build_converter({"ocr": "rapidocr", "lang": ["en"]})
md = conv.convert(sys.argv[2]).document.export_to_markdown(page_break_placeholder="<!-- page break -->")
open(sys.argv[3], "w", encoding="utf-8", newline="\n").write(md)
"""

MARKITDOWN = r"""
import sys
from markitdown import MarkItDown
md = MarkItDown().convert(sys.argv[2]).text_content
open(sys.argv[3], "w", encoding="utf-8", newline="\n").write(md)
"""


def _record(engine: str, script: str, out: Path, tmp_path: Path) -> str:
    require_engine(engine)
    s = tmp_path / f"rec_{engine}.py"
    s.write_text(script, encoding="utf-8")
    fresh = tmp_path / f"{engine}.md"
    env = {**os.environ, "AIDOC_MODELS_DIR": str(paths.models_dir()), "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([str(paths.venv_python(engine)), str(s), str(paths.runner_dir()), str(PDF), str(fresh)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=900,
                       check=False)
    assert r.returncode == 0, r.stderr[-2000:]
    text = fresh.read_text(encoding="utf-8")
    if not out.exists() or os.environ.get("AIDOC_RECORD") == "1":
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
    else:
        assert text == out.read_text(encoding="utf-8"), f"{engine} native output changed; re-record with AIDOC_RECORD=1"
    return text


def test_record_docling_native(tmp_path):
    md = _record("docling", DOCLING, FIX / "docling_recorded" / "furniture8" / "export.md", tmp_path)
    assert md.count("<!-- page break -->") >= 6
    r = verify_markdown(md, PDF, fmt="docling")
    assert r.level != "low"


def test_record_markitdown_native(tmp_path):
    md = _record("markitdown", MARKITDOWN, FIX / "markitdown_recorded" / "furniture8" / "convert.md", tmp_path)
    r = verify_markdown(md, PDF)
    assert r.page_map["method"] == "inferred" and r.page_map["anchored"] >= 5


def test_runs_on_this_python_only_through_engine_envs():
    assert Path(sys.executable).resolve() != paths.venv_python("docling").resolve()
