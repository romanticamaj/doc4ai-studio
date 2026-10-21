"""Sample registry of the real-sample evaluation (spec 2026-10-22 §2, §4.1–4.3). Stand-in folders only: the real
samples under tests/fixtures/manual/realeval are never opened by a test (spec §8.2)."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pymupdf
import pytest

from bench.realeval import samples

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _stand_in(tmp: Path, *, sha: str | None = None) -> Path:
    d = tmp / "zh-stand-in"
    (d / "gt").mkdir(parents=True)
    shutil.copy(FIX / "scanned_cht.pdf", d / "original.pdf")
    for p in (1, 2):
        (d / "gt" / f"page-{p:03d}.txt").write_text(f"第{p}頁", encoding="utf-8")
    entry = {"name": d.name, "group": "A", "role": "decision", "lang": "cht", "file": "original.pdf",
             "sha256": sha or _sha(d / "original.pdf"), "pdf_pages": 2, "gt_pages": [1, 2], "gt_level": 3,
             "layout": "horizontal", "spread": False, "ocr_text_layer": False}
    (tmp / "MANIFEST.json").write_text(json.dumps([entry]), encoding="utf-8")
    return tmp


def test_manifest_has_twelve_documents_and_no_text():
    man = json.loads(samples.MANIFEST.read_text(encoding="utf-8"))
    assert len(man) == 12
    groups = [e["group"] for e in man]
    assert (groups.count("A"), groups.count("B"), groups.count("C")) == (6, 4, 2)
    for e in man:
        assert len(e["sha256"]) == 64 and all(c in "0123456789abcdef" for c in e["sha256"])
        for v in e.values():
            if isinstance(v, str):
                assert len(v) <= 80, v
    assert [len(e["gt_pages"]) for e in man if e["group"] == "A"] == [9, 35, 17, 22, 4, 2]
    assert [len(e["gt_pages"]) for e in man if e["group"] == "C"] == [5, 16]
    assert [e["n_display"] for e in man if e["group"] == "B"] == [68, 18, 191, 110]
    ncl = next(e for e in man if e["name"] == "zh-ncl-renjian-cihua")
    assert ncl["gt_pages"] == [1, 3, *range(9, 42)]
    assert next(e for e in man if e["name"] == "zh-supreme-court-1972")["spread"] is True


def test_load_accepts_a_matching_stand_in(tmp_path):
    root = _stand_in(tmp_path)
    got, missing = samples.load(root, manifest=root / "MANIFEST.json", stand_in=True)
    assert missing == [] and [s.name for s in got] == ["zh-stand-in"]
    assert got[0].gt_text(2) == "第2頁" and got[0].scoring_pdf() == got[0].pdf


def test_load_rejects_sha_mismatch(tmp_path):
    root = _stand_in(tmp_path, sha="0" * 64)
    got, missing = samples.load(root, manifest=root / "MANIFEST.json", stand_in=True)
    assert got == [] and missing[0]["name"] == "zh-stand-in" and "sha256" in missing[0]["why"]


def test_load_reports_a_missing_gt_page(tmp_path):
    root = _stand_in(tmp_path)
    (root / "zh-stand-in" / "gt" / "page-002.txt").unlink()
    got, missing = samples.load(root, manifest=root / "MANIFEST.json", stand_in=True)
    assert got == [] and "page-002" in missing[0]["why"]


def test_load_refuses_tracked_folder(tmp_path, monkeypatch):
    def fake_run(cmd, *a, **k):
        if "ls-files" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="tests/fixtures/manual/realeval/x.pdf\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(samples.subprocess, "run", fake_run)
    with pytest.raises(SystemExit, match="tracked"):
        samples.load(samples.REAL_ROOT)


def test_load_refuses_a_folder_git_does_not_ignore(monkeypatch):
    def fake_run(cmd, *a, **k):
        return subprocess.CompletedProcess(cmd, 1 if "check-ignore" in cmd else 0, stdout="", stderr="")
    monkeypatch.setattr(samples.subprocess, "run", fake_run)
    with pytest.raises(SystemExit, match="ignored"):
        samples.guard_untracked(samples.REAL_ROOT)


def test_load_refuses_fake_mode(tmp_path):
    with pytest.raises(SystemExit, match="never loaded in --fake mode"):
        samples.load(tmp_path / "does-not-exist", fake=True)


def test_stand_in_mode_refuses_the_real_folder():
    with pytest.raises(SystemExit, match="stand-in"):
        samples.load(samples.REAL_ROOT, manifest=samples.MANIFEST, stand_in=True)


def test_render_image_only_strips_text_layer(tmp_path):
    from aidoc.probe import probe_file
    dst = tmp_path / "derived" / "image_only.pdf"
    info = samples.render_image_only(FIX / "text.pdf", dst, dpi=72)
    with pymupdf.open(FIX / "text.pdf") as a, pymupdf.open(dst) as b:
        assert b.page_count == a.page_count == info["pages"]
        assert sum(len(p.get_text().strip()) for p in b) == 0
        for pa, pb in zip(a, b, strict=True):
            assert abs(pa.rect.width - pb.rect.width) < 0.5 and abs(pa.rect.height - pb.rect.height) < 0.5
    assert info["text_chars"] == 0 and info["sha256"] == _sha(dst)
    assert probe_file(dst).text_ratio == 0


def test_control_scores_the_derived_pdf(tmp_path):
    root = _stand_in(tmp_path)
    man = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    man[0].update(group="C", role="control", lang="en", ocr_text_layer=True)
    (root / "MANIFEST.json").write_text(json.dumps(man), encoding="utf-8")
    got, _ = samples.load(root, manifest=root / "MANIFEST.json", stand_in=True)
    assert got[0].scoring_pdf() == root / "zh-stand-in" / "derived" / "image_only.pdf"
