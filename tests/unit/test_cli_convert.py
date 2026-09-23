import json
import shutil
from aidoc.cli import main
from tests.fakes.scenario import write_scenario, fake_env


def test_convert_text_output(tmp_root, fixtures, monkeypatch, capsys):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    src = tmp_root / "text.pdf"
    shutil.copy(fixtures / "text.pdf", src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out")]) == 0
    out = capsys.readouterr().out
    assert "done" in out and "docling" in out and str(tmp_root / "out" / "text") in out


def test_convert_json_and_failed_exit(tmp_root, fixtures, monkeypatch, capsys):
    fake_env(monkeypatch, write_scenario(tmp_root / "sc.json"))
    src = tmp_root / "corrupt.pdf"
    shutil.copy(fixtures / "corrupt.pdf", src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_convert_missing_file(tmp_root, capsys):
    assert main(["convert", str(tmp_root / "nope.pdf")]) == 1
    assert "not found" in capsys.readouterr().err.lower()
