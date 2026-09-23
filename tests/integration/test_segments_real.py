import json
import re
import shutil

import pytest

from aidoc.cli import main

pytestmark = pytest.mark.slow


def _runner_pids(capsys_err: str) -> list[str]:
    return re.findall(r"runner started pid=(\d+)", capsys_err)


def test_big_pdf_segmented_with_docling(tmp_root, fixtures, capsys):
    src = tmp_root / "big.pdf"; shutil.copy(fixtures / "big.pdf", src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--json", "-v"]) == 0
    err = capsys.readouterr().err
    sc = json.loads((tmp_root / "out" / "big" / "big.json").read_text(encoding="utf-8"))
    assert sc["segments"] == 2 and sc["pages"] == 45 and sc["quality"]["level"] == "ok" and sc["engine"] == "docling"
    md = (tmp_root / "out" / "big" / "big.md").read_text(encoding="utf-8")
    # Docling classifies the page-top line "第 N 頁 / Page N" as page-header furniture and drops it on every page
    # (P1 behaviour too), so check that each page's body text arrived, including all of segment 2.
    assert "<!-- page: 41 -->" in md and md.count("<!-- page: ") == 45
    assert md.count("This is an English paragraph") == 45 and md.index("<!-- page: 45 -->") < md.rindex("English")
    assert len(_runner_pids(err)) == 1                        # one runner process served both segments


def test_big_pdf_segmented_with_mineru_forced(tmp_root, fixtures, capsys):
    src = tmp_root / "big.pdf"; shutil.copy(fixtures / "big.pdf", src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--engine", "mineru", "--json", "-v"]) == 0
    err = capsys.readouterr().err
    sc = json.loads((tmp_root / "out" / "big" / "big.json").read_text(encoding="utf-8"))
    assert sc["segments"] == 2 and sc["quality"]["level"] == "ok"
    md = (tmp_root / "out" / "big" / "big.md").read_text(encoding="utf-8")
    assert "<!-- page: 41 -->" in md and "第 45 頁" in md
    assert len(_runner_pids(err)) == 1
