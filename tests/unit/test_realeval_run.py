"""run.py orchestration of the real-sample evaluation (spec 2026-10-22 §3, §7.0, §8). Synthetic stand-ins, fake
engines (AIDOC_FAKE_ENGINES) and the fake VLM endpoint only — the real folder is never opened."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bench import run as B
from bench.realeval import run as R
from bench.realeval import samples as SM
from tests.fakes.vlm_server import FakeVlmServer

FIX = Path(__file__).resolve().parents[1] / "fixtures"
ZH = "這是一份用來測試文件轉換流程的繁體中文段落。內容包含標題、表格與圖片，用於驗證引擎的中文辨識能力。"
EN = ("This is an English paragraph used to test the document conversion pipeline. "
      "It contains enough words to exceed the per-page threshold.")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build_stand_in(root: Path) -> Path:
    """A folder shaped like tests/fixtures/manual/realeval, built from synthetic fixtures."""
    root.mkdir(parents=True, exist_ok=True)
    man = []
    zh = root / "zh-stand-in"
    (zh / "gt").mkdir(parents=True)
    shutil.copy(FIX / "scanned_cht.pdf", zh / "original.pdf")
    for p in (1, 2):
        (zh / "gt" / f"page-{p:03d}.txt").write_text(ZH * 4, encoding="utf-8")
    man.append({"name": zh.name, "group": "A", "role": "decision", "lang": "cht", "file": "original.pdf",
                "sha256": _sha(zh / "original.pdf"), "pdf_pages": 2, "gt_pages": [1, 2], "gt_level": 3,
                "layout": "horizontal", "spread": False, "ocr_text_layer": False})
    ax = root / "arxiv-stand-in"
    (ax / "gt").mkdir(parents=True)
    shutil.copy(FIX / "formula.pdf", ax / "paper.pdf")
    (ax / "gt" / "formulas.json").write_text(json.dumps(
        [{"display": "block", "latex": "E = mc^2"}, {"display": "inline", "latex": "x"}]), encoding="utf-8")
    (ax / "paper.html").write_text(f"<html><body><p>{EN}</p><math alttext='x'>x</math></body></html>",
                                   encoding="utf-8")
    man.append({"name": ax.name, "group": "B", "role": "decision", "lang": "en", "file": "paper.pdf",
                "sha256": _sha(ax / "paper.pdf"), "pdf_pages": 2, "gt": "gt/formulas.json", "html": "paper.html",
                "gt_level": "machine", "n_display": 1, "n_formulas": 2, "ocr_text_layer": False})
    en = root / "en-aashto-usrn-1984"                     # the control's real name: validity check 3 looks for it
    (en / "gt").mkdir(parents=True)
    shutil.copy(FIX / "text.pdf", en / "original.pdf")
    for p in (1, 2, 3):
        (en / "gt" / f"page-{p:03d}.txt").write_text(EN, encoding="utf-8")
    man.append({"name": en.name, "group": "C", "role": "control", "lang": "en", "file": "original.pdf",
                "sha256": _sha(en / "original.pdf"), "pdf_pages": 3, "gt_pages": [1, 2, 3], "gt_level": 4,
                "layout": "horizontal", "spread": False, "ocr_text_layer": True})
    (root / "MANIFEST.json").write_text(json.dumps(man), encoding="utf-8")
    (root / "gt_errata.json").write_text("[]", encoding="utf-8")
    (root / "gt_quality.json").write_text(json.dumps({"cer_upper": 0.01, "margin": 0.05, "alignment_ok": True,
                                                      "formula_gt_low_confidence": False}), encoding="utf-8")
    shutil.copy(FIX / "scanned_en.pdf", root / "cover.pdf")
    return root


@pytest.fixture(scope="module")
def dry_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("realeval")
    stand_in = build_stand_in(tmp / "stand-in")
    out = tmp / "run"
    seen: list[Path] = []
    real_load = SM.load

    def spy(root, **kw):
        seen.append(Path(root))
        return real_load(root, **kw)

    mp = pytest.MonkeyPatch()
    mp.setattr(R.samples, "load", spy)
    try:
        with FakeVlmServer({"default": "ok"}) as url:
            rc = R.run(["--part", "all", "--fake", "--stand-in", str(stand_in), "--out", str(out),
                        "--vlm-base-url", url])
    finally:
        mp.undo()
    return {"rc": rc, "out": out, "stand_in": stand_in, "seen": seen}


def test_fake_dry_run_writes_everything(dry_run):
    out = dry_run["out"]
    assert dry_run["rc"] == 0
    for f in ("env.json", "pages.jsonl", "results.jsonl", "vlm.jsonl", "validity.json", "decisions.json",
              "summary.md", "gt_quality.json"):
        assert (out / f).is_file(), f
    assert dry_run["seen"] and all(p == dry_run["stand_in"] for p in dry_run["seen"])
    dec = json.loads((out / "decisions.json").read_text(encoding="utf-8"))
    assert [dec[k]["verdict"] for k in ("zh_scan_route", "formula_route", "vlm_local")] == ["insufficient"] * 3
    assert dec["zh_scan_route"]["conditions"]["n_pages"]["value"] == 2
    rows = [json.loads(x) for x in (out / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {(r["config"], r["doc"]) for r in rows} == {(c, d) for c in R.CONFIGS
                                                       for d in ("zh-stand-in", "arxiv-stand-in",
                                                                 "en-aashto-usrn-1984")}
    ax = next(r for r in rows if r["doc"] == "arxiv-stand-in" and r["config"] == "auto")
    assert {"R", "S", "R_inline", "T", "math_hint", "route_reason"} <= set(ax)


def test_dry_run_env_records_identities(dry_run):
    env = json.loads((dry_run["out"] / "env.json").read_text(encoding="utf-8"))
    assert env["fake"] is True and env["reruns"] == []
    assert set(env["samples"]) == {"zh-stand-in", "arxiv-stand-in", "en-aashto-usrn-1984"}
    assert env["derived"]["en-aashto-usrn-1984"]["text_chars"] == 0
    assert len(env["gt_quality_sha256"]) == 64 and env["spec_commit"] and "harness_commit" in env


def test_dry_run_configs_keep_the_vlm_backend_off(dry_run):
    for c in R.CONFIGS:
        text = (dry_run["out"] / f"aidoc-{c}.toml").read_text(encoding="utf-8")
        assert 'backend = "none"' in text


def test_dry_run_scores_the_derived_control(dry_run):
    env = json.loads((dry_run["out"] / "env.json").read_text(encoding="utf-8"))
    derived = dry_run["stand_in"] / "en-aashto-usrn-1984" / "derived" / "image_only.pdf"
    assert derived.is_file() and env["derived"]["en-aashto-usrn-1984"]["sha256"] == _sha(derived)
    staged = dry_run["out"] / "inputs" / "en-aashto-usrn-1984.pdf"
    assert _sha(staged) == _sha(derived)


def test_dry_run_validity_file(dry_run):
    v = json.loads((dry_run["out"] / "validity.json").read_text(encoding="utf-8"))
    assert set(v["checks"]) == {"samples", "clean_tree", "control_ned", "auto_route", "vlm_setup"}
    assert v["checks"]["vlm_setup"] is False                 # the fake endpoint is not a pinned llama-server


def test_decide_only_matches(dry_run, capsys):
    assert R.run(["--decide-only", "--out", str(dry_run["out"])]) == 0
    assert "no diff" in capsys.readouterr().out


def test_decide_only_reports_a_diff(dry_run, tmp_path, capsys):
    run = tmp_path / "copy"
    shutil.copytree(dry_run["out"], run, ignore=shutil.ignore_patterns("data-*", "out-*", "inputs"))
    dec = json.loads((run / "decisions.json").read_text(encoding="utf-8"))
    dec["zh_scan_route"]["verdict"] = "change"
    (run / "decisions.json").write_text(json.dumps(dec), encoding="utf-8")
    assert R.run(["--decide-only", "--out", str(run)]) == 1
    assert "zh_scan_route" in capsys.readouterr().out


def test_resume_converts_nothing_twice(dry_run, monkeypatch):
    monkeypatch.setattr(R.B, "convert", lambda *a, **k: pytest.fail("converted again"))
    assert R.run(["--part", "scans", "--fake", "--stand-in", str(dry_run["stand_in"]), "--out",
                  str(dry_run["out"]), "--resume"]) == 0


def test_a_second_run_without_resume_is_refused(dry_run):
    with pytest.raises(SystemExit, match="--resume"):
        R.run(["--part", "scans", "--fake", "--stand-in", str(dry_run["stand_in"]), "--out", str(dry_run["out"])])


def test_fake_mode_never_loads_real_samples(tmp_path):
    with pytest.raises(SystemExit, match="stand-in"):
        R.run(["--part", "scans", "--fake", "--out", str(tmp_path / "x")])


def test_dirty_tree_is_refused_for_real_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "base_environment", lambda a: {"aidoc_dirty": True, "aidoc_commit": "x"})
    monkeypatch.setattr(R.samples, "load", lambda *a, **k: pytest.fail("real samples opened"))
    monkeypatch.setattr(R.B, "convert", lambda *a, **k: pytest.fail("converted"))
    with pytest.raises(SystemExit, match="dirty"):
        R.run(["--part", "scans", "--out", str(tmp_path / "x")])


def test_real_run_needs_gt_quality_before_any_conversion(tmp_path, monkeypatch):
    stand_in = build_stand_in(tmp_path / "s")
    (stand_in / "gt_quality.json").unlink()
    monkeypatch.setattr(R, "base_environment", lambda a: {"aidoc_dirty": False, "aidoc_commit": "x"})
    monkeypatch.setattr(R.B, "convert", lambda *a, **k: pytest.fail("converted"))
    with pytest.raises(SystemExit, match="gt_quality.json"):
        R.run(["--part", "scans", "--root", str(stand_in), "--out", str(tmp_path / "x")])


def test_real_vlm_part_launches_the_pinned_server(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "base_environment", lambda a: {"aidoc_dirty": False, "aidoc_commit": "x"})
    with pytest.raises(SystemExit, match="loopback|--vlm-artefacts"):
        R.run(["--part", "vlm", "--out", str(tmp_path / "x"), "--vlm-base-url", "http://127.0.0.1:1/v1"])


def test_convert_passes_extra_args(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
    monkeypatch.setattr(B.subprocess, "run", fake_run)
    monkeypatch.setattr(B, "Sampler", _NoSampler)
    s = B.Sample("x", "R", FIX / "text.pdf", None, "realeval")
    B.convert(s, "docling", tmp_path, fake=True, extra_args=("--lang", "cht"))
    assert seen["cmd"][-2:] == ["--lang", "cht"] and "--engine" in seen["cmd"]
    B.convert(s, "auto", tmp_path, fake=True)
    assert "--lang" not in seen["cmd"]


class _NoSampler:
    peak = baseline = delta = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def test_route_reason_is_the_routers_rule():
    from aidoc.probe import probe_file
    from aidoc.router import _rule
    assert R.route_reason(FIX / "scanned_cht.pdf", "cht") == "pdf_scanned"
    probe = probe_file(FIX / "formula.pdf")
    assert R.route_reason(FIX / "formula.pdf", "en") == _rule(probe, R._opts("en"))[1]


def test_validity_checks_on_hand_made_rows():
    env = {"aidoc_dirty": False, "commit_has_spec_and_harness": True, "missing": [],
           "derived": {"en-aashto-usrn-1984": {"text_chars": 0}}, "expected_samples": 2, "samples": {"a": 1, "b": 2}}
    rows = [{"config": "docling", "doc": "en-aashto-usrn-1984", "group": "C", "ned": 0.86},
            {"config": "mineru", "doc": "en-aashto-usrn-1984", "group": "C", "ned": 0.5},
            {"config": "auto", "doc": "zh-a", "group": "A", "tried": ["mineru", "docling"], "route_reason": "pdf_scanned"}]
    vlm = {"server": {"device_cuda": True, "layers_on_gpu": 18, "layers_total": 18}, "endpoint_host": "127.0.0.1",
           "pinned_ok": True}
    v = R.validity(env, rows, vlm)
    assert v["checks"] == {"samples": True, "clean_tree": True, "control_ned": True, "auto_route": True,
                           "vlm_setup": True}
    rows[0]["ned"] = 0.84
    rows[2]["tried"] = ["docling"]
    v = R.validity({**env, "aidoc_dirty": True}, rows, {**vlm, "pinned_ok": False})
    assert not any(v["checks"][k] for k in ("clean_tree", "control_ned", "auto_route", "vlm_setup"))


# ---------------------------------------------------------------- final-review fixes

def _clean_env(a):
    return {"aidoc_dirty": False, "aidoc_commit": "HEAD"}


def test_validity_auto_route_ignores_failed_conversions_but_checks_the_reason():
    env = {"aidoc_dirty": False, "commit_has_spec_and_harness": True, "missing": [], "derived": {},
           "expected_samples": 0, "samples": {}}
    rows = [{"config": "auto", "doc": "zh-a", "group": "A", "tried": ["mineru"], "route_reason": "pdf_scanned"},
            {"config": "auto", "doc": "zh-b", "group": "A", "tried": [], "status": "failed",
             "route_reason": "pdf_scanned"}]
    assert R.validity(env, rows, None)["checks"]["auto_route"] is True
    rows[1]["route_reason"] = "pdf_default"
    assert R.validity(env, rows, None)["checks"]["auto_route"] is False


def test_real_run_refuses_missing_samples_before_converting(tmp_path, monkeypatch):
    stand_in = build_stand_in(tmp_path / "s")
    monkeypatch.setattr(R, "base_environment", _clean_env)
    monkeypatch.setattr(R.samples, "load", lambda *a, **k: ([], [{"name": "zh-x", "why": "sha256 mismatch"}]))
    monkeypatch.setattr(R.B, "convert", lambda *a, **k: pytest.fail("converted"))
    with pytest.raises(SystemExit, match="missing"):
        R.run(["--part", "scans", "--root", str(stand_in), "--out", str(tmp_path / "x")])


def test_real_run_refuses_a_commit_without_spec_and_harness(tmp_path, monkeypatch):
    stand_in = build_stand_in(tmp_path / "s")
    docs, _ = SM.load(stand_in, manifest=stand_in / "MANIFEST.json", stand_in=True)
    monkeypatch.setattr(R, "base_environment", _clean_env)
    monkeypatch.setattr(R.samples, "load", lambda *a, **k: (docs, []))
    monkeypatch.setattr(R, "_commit_has_spec_and_harness", lambda head: False)
    monkeypatch.setattr(R.B, "convert", lambda *a, **k: pytest.fail("converted"))
    with pytest.raises(SystemExit, match="spec and harness"):
        R.run(["--part", "scans", "--root", str(stand_in), "--out", str(tmp_path / "x")])


def test_real_run_refuses_a_failed_alignment(tmp_path, monkeypatch):
    stand_in = build_stand_in(tmp_path / "s")
    q = json.loads((stand_in / "gt_quality.json").read_text(encoding="utf-8"))
    q["alignment_ok"] = False
    (stand_in / "gt_quality.json").write_text(json.dumps(q), encoding="utf-8")
    monkeypatch.setattr(R, "base_environment", _clean_env)
    monkeypatch.setattr(R.samples, "load", lambda *a, **k: pytest.fail("samples opened"))
    with pytest.raises(SystemExit, match="alignment"):
        R.run(["--part", "scans", "--root", str(stand_in), "--out", str(tmp_path / "x")])


def test_decisions_carry_the_whole_route_input(dry_run):
    dec = json.loads((dry_run["out"] / "decisions.json").read_text(encoding="utf-8"))
    route = dec["zh_scan_route"]["route"]
    assert set(route) >= {"delta", "lo", "hi", "per_doc", "wins_D", "wins_M", "big_loss_D", "big_loss_M"}
    assert "zh-stand-in" in route["per_doc"]
