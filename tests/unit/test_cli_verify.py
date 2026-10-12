import json
import subprocess
import sys
from pathlib import Path

import pymupdf

from aidoc.cli import main
from aidoc.names import file_sha256
from aidoc.pagemap import MARKER, split_pages

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _own(pdf):
    with pymupdf.open(pdf) as d:
        return "".join(f"<!-- page: {i} -->\n\n{p.get_text().strip()}\n\n" for i, p in enumerate(d, 1))


def test_exit_codes(tmp_path, capsys):
    ok = tmp_path / "ok.md"
    ok.write_text(_own(FIX / "paged_furniture.pdf"), encoding="utf-8")
    assert main(["verify", str(ok), "--pdf", str(FIX / "paged_furniture.pdf")]) == 0
    out = capsys.readouterr().out
    assert "level: ok" in out and len(out.strip().splitlines()) <= 12
    bad = tmp_path / "bad.md"
    bad.write_text(MARKER.sub(lambda m: f"<!-- page: {int(m.group(1)) + 1} -->", ok.read_text(encoding="utf-8")),
                   encoding="utf-8")
    assert main(["verify", str(bad), "--pdf", str(FIX / "paged_furniture.pdf")]) == 1
    scan = tmp_path / "scan.md"
    scan.write_text("沒有頁碼的 OCR 文字", encoding="utf-8")
    assert main(["verify", str(scan), "--pdf", str(FIX / "scanned_cht.pdf")]) == 3
    assert main(["verify", str(scan), "--pdf", str(FIX / "scanned_cht.pdf"), "--allow-unverifiable"]) == 0
    assert main(["verify", str(tmp_path / "missing.md"), "--pdf", str(FIX / "text.pdf")]) == 4
    assert main(["verify", str(ok), "--pdf", str(FIX / "corrupt.pdf")]) == 4
    assert main(["verify", str(ok), "--pdf", str(FIX / "encrypted.pdf")]) == 4
    assert main(["verify", str(ok), "--pdf", str(FIX / "text.pdf"), "--page-marker-regex", r"\[\[page\]\]"]) == 4
    assert main(["verify", str(scan)]) == 3                          # no PDF, no --pages: unverifiable
    capsys.readouterr()


def test_fail_on_warn_and_json_output(tmp_path, capsys):
    md = _own(FIX / "paged_furniture.pdf")
    _, secs = split_pages(md)
    p = tmp_path / "w.md"
    p.write_text(md.replace(secs[3], "\n\n"), encoding="utf-8")
    pdf = str(FIX / "paged_furniture.pdf")
    assert main(["verify", str(p), "--pdf", pdf]) == 0                                   # warn passes by default
    assert main(["verify", str(p), "--pdf", pdf, "--fail-on", "warn"]) == 1
    assert main(["verify", str(p), "--pdf", pdf, "--json", str(tmp_path / "r.json")]) == 0
    j = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert j["summary"]["level"] == "warn" and j["pages"][0]["reasons"] == ["page_dropped"]
    capsys.readouterr()
    assert main(["verify", str(p), "--pdf", pdf, "--json", "-"]) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["verify_version"] == 1 and "level: warn" in cap.err
    assert main(["verify", str(p), "--pdf", pdf, "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert "page_dropped 1" in out and "worst pages:" in out


def test_json_written_for_unverifiable(tmp_path):
    scan = tmp_path / "scan.md"
    scan.write_text("沒有頁碼的 OCR 文字", encoding="utf-8")
    assert main(["verify", str(scan), "--pdf", str(FIX / "scanned_cht.pdf"), "--json", str(tmp_path / "r.json")]) == 3
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["verifiable"] is False


def test_output_directory_form_uses_sidecar(tmp_path):
    out = tmp_path / "out" / "text"
    out.mkdir(parents=True)
    (out / "text.md").write_text(_own(FIX / "text.pdf"), encoding="utf-8")
    (out / "text.json").write_text(json.dumps({"source": str(FIX / "text.pdf"), "sha256": file_sha256(FIX / "text.pdf"),
                                               "pages": 3, "engine": "docling",
                                               "quality": {"level": "ok", "score": 1.0}}), encoding="utf-8")
    assert main(["verify", str(out), "--json", str(tmp_path / "r.json")]) in (0, 1)
    j = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert j["pdf"]["pages"] == 3 and j["previous"]["engine"] == "docling" and j["verifiable"] is True


def test_output_directory_without_source_is_partial(tmp_path):
    out = tmp_path / "out" / "text"
    out.mkdir(parents=True)
    (out / "text.md").write_text(_own(FIX / "text.pdf"), encoding="utf-8")
    (out / "text.json").write_text(json.dumps({"source": "text.pdf", "sha256": "0" * 64, "pages": 3,
                                               "engine": "docling", "quality": {"level": "ok"}}), encoding="utf-8")
    assert main(["verify", str(out), "--json", str(tmp_path / "r.json")]) == 0
    j = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert j["verifiable"] == "partial" and j["pdf"] is None and j["page_map"]["coverage"] == 1.0
    assert main(["verify", str(tmp_path)]) == 4                     # a directory that is not an output dir


def test_subprocess_entry_point(tmp_path):
    p = tmp_path / "a.md"
    p.write_text(_own(FIX / "paged_furniture.pdf"), encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "aidoc.cli", "verify", str(p), "--pdf", str(FIX / "paged_furniture.pdf")],
                       capture_output=True, text=True, encoding="utf-8", check=False)
    assert r.returncode == 0 and "level: ok" in r.stdout
