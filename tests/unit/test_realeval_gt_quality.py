"""Ground-truth quality tool (spec 2026-10-22 §6): seeded spot-check draw, Wilson upper bound, margin, summary."""
from __future__ import annotations

import json

import pytest

from bench.realeval import gt_quality as G


def _manifest():
    m = [{"name": f"zh-{i}", "group": "A", "gt_pages": list(range(1, 4 + i * 3))} for i in range(6)]
    m += [{"name": "arxiv-a", "group": "B", "n_display": 18}, {"name": "arxiv-b", "group": "B", "n_display": 68}]
    m += [{"name": "en-c", "group": "C", "gt_pages": [1, 2, 3]}]
    return m


def test_draw_is_seeded_and_stratified():
    m = _manifest()
    d = G.draw(m)
    assert list(d) == [f"zh-{i}" for i in range(6)]
    assert sum(len(v) for v in d.values()) == 12 and all(len(v) == 2 and len(set(v)) == 2 for v in d.values())
    assert d == G.draw(m)
    for e in m[:6]:
        assert set(d[e["name"]]) <= set(e["gt_pages"])
    assert G.draw(m, seed=1) != d


def test_draw_on_the_real_manifest_needs_no_sample_file():
    from bench.realeval.samples import MANIFEST
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    d = G.draw(m)
    assert len(d) == 6 and sum(len(v) for v in d.values()) == 12
    f = G.draw_formulas(m)
    assert len(f) == 4 and all(len(v) == 3 for v in f.values())
    assert all(0 <= i < e["n_display"] for e in m if e["group"] == "B" for i in f[e["name"]])


def test_wilson_upper_and_margin():
    up = G.wilson_upper(0, 2000)
    assert 0 < up < 0.002 and G.margin(up) == 0.05
    up = G.wilson_upper(60, 2000)
    assert up > 0.025 and G.margin(up) == 0.10
    assert G.wilson_upper(0, 0) is None


def test_formula_gt_low_confidence():
    assert G.formula_low_confidence([{"ok": False}] * 3 + [{"ok": True}] * 9) is True
    assert G.formula_low_confidence([{"ok": False}] * 2 + [{"ok": True}] * 10) is False


def _write_inputs(root, m, *, tamper=False):
    d = G.draw(m)
    spot = [{"doc": doc, "page": p, "k": 1, "n": 200} for doc, ps in d.items() for p in ps]
    if tamper:
        spot[0]["page"] = 999
    (root / "gt_spotcheck.json").write_text(json.dumps(spot), encoding="utf-8")
    (root / "gt_errata.json").write_text(json.dumps([{"doc": "zh-0", "page": 1, "wrong": "a", "right": "b",
                                                      "source": "SOURCE.md"}]), encoding="utf-8")
    (root / "alignment.json").write_text(json.dumps([{"doc": e["name"], "first_ok": True, "last_ok": True}
                                                     for e in m if e["group"] in "AC"]), encoding="utf-8")
    f = G.draw_formulas(m)
    (root / "formula_spotcheck.json").write_text(json.dumps([{"doc": doc, "index": i, "ok": True}
                                                             for doc, ix in f.items() for i in ix]), encoding="utf-8")


def test_summarise_writes_gt_quality(tmp_path):
    m = _manifest()
    (tmp_path / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
    _write_inputs(tmp_path, m)
    q = G.summarise(tmp_path, manifest=tmp_path / "MANIFEST.json", author="verifier")
    assert q["k"] == 12 and q["n"] == 2400 and q["cer"] == pytest.approx(0.005)
    assert q["cer_upper"] == pytest.approx(G.wilson_upper(12, 2400)) and q["margin"] == 0.05
    assert q["errata_count"] == 1 and q["alignment_ok"] is True and q["formula_gt_low_confidence"] is False
    assert q["author"] == "verifier" and q["date"]
    assert json.loads((tmp_path / "gt_quality.json").read_text(encoding="utf-8")) == q


def test_summarise_refuses_pages_that_were_not_drawn(tmp_path):
    m = _manifest()
    (tmp_path / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
    _write_inputs(tmp_path, m, tamper=True)
    with pytest.raises(SystemExit, match="drawn"):
        G.summarise(tmp_path, manifest=tmp_path / "MANIFEST.json", author="v")


def test_summarise_marks_a_failed_alignment(tmp_path):
    m = _manifest()
    (tmp_path / "MANIFEST.json").write_text(json.dumps(m), encoding="utf-8")
    _write_inputs(tmp_path, m)
    al = json.loads((tmp_path / "alignment.json").read_text(encoding="utf-8"))
    al[0]["last_ok"] = False
    (tmp_path / "alignment.json").write_text(json.dumps(al), encoding="utf-8")
    assert G.summarise(tmp_path, manifest=tmp_path / "MANIFEST.json", author="v")["alignment_ok"] is False


def test_cli_draw_prints_pages_and_formula_indices(capsys):
    assert G.main(["--draw"]) == 0
    out = capsys.readouterr().out
    assert "zh-kaohsiung-court-1973" in out and "arxiv-2506.00451" in out
