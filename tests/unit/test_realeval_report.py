"""summary.md and the committed record skeleton: numbers only, never document text (spec 2026-10-22 §9, §10)."""
from __future__ import annotations

import json
from pathlib import Path

from bench.realeval import report, rules

SENTENCE = "這是一段四十個字元長的唯一測試句子絕對不可以出現在任何報告輸出裡面否則就是洩漏了文件內容喔"
assert len(SENTENCE) >= 40


def _jsonl(p: Path, rows: list[dict]) -> None:
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _run(tmp: Path) -> Path:
    run = tmp / "run"
    run.mkdir()
    (run / "env.json").write_text(json.dumps({
        "date": "2026-10-23T10:00:00+08:00", "aidoc_commit": "abc123def456", "aidoc_dirty": False,
        "spec_commit": "abc123def456", "gt_quality_sha256": "f" * 64, "samples": {"zh-a": "1" * 64},
        "derived": {"en-c": {"sha256": "2" * 64, "text_chars": 0, "pages": 2}}, "gpu": {"name": "GPU"},
        "vlm_pinned": {"gguf": True}, "offline_declared": True, "reruns": [], "fake": True}), encoding="utf-8")
    res = []
    for cfg, ned in (("auto", 0.8), ("docling", 0.9), ("mineru", 0.7)):
        res.append({"config": cfg, "doc": "zh-a", "group": "A", "status": "done", "engine_used": "mineru",
                    "tried": ["mineru"], "ned": ned, "cer": 1 - ned, "f1": ned, "n_pages": 2,
                    "worst": {"page": 2, "ned": ned - 0.1}, "seconds": 3.0})
        res.append({"config": cfg, "doc": "en-c", "group": "C", "status": "done", "engine_used": cfg, "tried": [],
                    "ned": 0.95, "cer": 0.05, "f1": 0.97, "n_pages": 2, "worst": {"page": 1, "ned": 0.9}})
        res.append({"config": cfg, "doc": "arxiv-x", "group": "B", "status": "done", "engine_used": "mineru",
                    "tried": ["mineru"], "R": 0.61, "S": 0.82, "R_inline": 0.4, "T": 0.93, "n_display": 18,
                    "math_hint": True, "route_reason": "pdf_math"})
    _jsonl(run / "results.jsonl", res)
    _jsonl(run / "pages.jsonl", [{"config": "docling", "doc": "zh-a", "page": 1, "ned": 0.9}])
    _jsonl(run / "vlm.jsonl", [
        {"kind": "page", "doc": "zh-a", "page": 1, "recall": 0.97, "common": 97, "gt_len": 100, "seconds": 1.1,
         "finish_reason": "stop"},
        {"kind": "page", "doc": "zh-a", "page": 2, "recall": 0.95, "common": 95, "gt_len": 100, "seconds": 2.1,
         "finish_reason": "length"},
        {"kind": "cover", "hit": True, "seconds": 0.5},
        {"kind": "summary", "per_doc": {"zh-a": {"recall": 0.96, "pages": 2, "max_sec": 2.1, "mean_sec": 1.6,
                                                 "length_pages": 1, "complete": True}},
         "max_sec_overall": 2.1, "cover_hit": True, "vram": {"baseline": 900, "peak": 3300, "delta": 2400},
         "load_seconds": 8.5}])
    (run / "gt_quality.json").write_text(json.dumps({
        "pages": [{"doc": "zh-a", "page": 1, "k": 2, "n": 900}], "k": 2, "n": 900, "cer": 0.0022,
        "cer_upper": 0.008, "margin": 0.05, "formula_gt_low_confidence": False, "errata_count": 1,
        "alignment_ok": True, "author": "verifier", "date": "2026-10-22"}), encoding="utf-8")
    route = {"delta": 0.2, "lo": 0.1, "hi": 0.3, "n_pages": 2, "n_docs": 1, "n_docs_both": 1, "wins_D": 1,
             "wins_M": 0, "big_loss_D": 0, "big_loss_M": 0, "per_doc": {"zh-a": 0.2}}
    dec = rules.decide(route, [{"doc": "arxiv-x", "R_M": 0.61, "R_D": 0.0, "T_M": 0.9, "T_D": 0.9, "n_display": 18,
                                "math_hint": True, "auto_reason": "pdf_math", "auto_engine": "mineru",
                                "both_ok": True}],
                       None, {"cer_upper": 0.008, "formula_gt_low_confidence": False},
                       {"checks": {"samples": True}})
    (run / "decisions.json").write_text(json.dumps(dec), encoding="utf-8")
    # document text exists in the run folder (outputs) and must never reach the summary
    md = run / "out-docling" / "zh-a" / "zh-a.md"
    md.parent.mkdir(parents=True)
    md.write_text(f"<!-- page: 1 -->\n{SENTENCE}\n", encoding="utf-8")
    return run


HEADINGS = ["## 環境與凍結", "## GT 信心", "## 表 A", "## 表 B", "## 表 C", "## 表 V", "## 決策", "## 重跑",
            "## 不宣稱"]


def test_summary_has_fixed_sections(tmp_path):
    text = report.summary(_run(tmp_path))
    pos = [text.index(h) for h in HEADINGS]
    assert pos == sorted(pos)
    assert text.count(report.NONCLAIMS) == 5     # after each table group (A, B, C, V) + the 不宣稱 section
    assert report.EVAL_NONCLAIMS in text


def test_record_skeleton_adds_deviations_and_commentary(tmp_path):
    text = report.record_skeleton(_run(tmp_path))
    for h in [*HEADINGS, "## 偏差", "## 評註"]:
        assert h in text
    assert text.index("## 重跑") < text.index("## 偏差") < text.index("## 不宣稱")


def test_summary_contains_no_document_text(tmp_path):
    run = _run(tmp_path)
    gt = tmp_path / "zh-a" / "gt"
    gt.mkdir(parents=True)
    (gt / "page-001.txt").write_text(SENTENCE, encoding="utf-8")
    for text in (report.summary(run), report.record_skeleton(run)):
        n = rules.SNIPPET_MAX + 1
        assert not any(SENTENCE[i:i + n] in text for i in range(len(SENTENCE) - n + 1))


def test_decisions_rendered_with_conditions(tmp_path):
    run = _run(tmp_path)
    text = report.summary(run)
    dec = json.loads((run / "decisions.json").read_text(encoding="utf-8"))
    for key in ("zh_scan_route", "formula_route", "vlm_local"):
        assert f"`{dec[key]['verdict']}`" in text
        for name, c in dec[key]["conditions"].items():
            assert f"| {name} |" in text
            assert report.fmt(c["value"]) in text and report.fmt(c["threshold"]) in text


def test_tables_show_the_numbers(tmp_path):
    text = report.summary(_run(tmp_path))
    assert "| zh-a | 0.800 | 0.900 | 0.700 |" in text          # NED auto / docling / mineru
    assert "p2 (0.800)" in text                              # docling worst page, number only
    assert "| arxiv-x | mineru | 0.610 | 0.820 | 0.930 |" in text
    assert "| zh-a | 0.960 | 2 | 2.10 | 1.60 | 1 |" in text
    assert "2400" in text and "8.5" in text


def test_gt_confidence_block(tmp_path):
    text = report.summary(_run(tmp_path))
    block = text[text.index("## GT 信心"):text.index("## 表 A")]
    for s in ("等級", "抽查頁數 | 1", "字數 | 900", "k | 2", "0.0022", "0.0080", "margin | 0.05", "勘誤 | 1"):
        assert s in block, s


def test_write_summary(tmp_path):
    run = _run(tmp_path)
    p = report.write_summary(run)
    assert p == run / "summary.md" and p.read_text(encoding="utf-8").startswith("# ")
