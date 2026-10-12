import json
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2] / "bench"
FIX = Path(__file__).resolve().parents[1] / "fixtures"
ENV = {"gpu": {"name": "RTX 5070 Ti", "driver": "591.86"}, "date": "2026-10-05", "aidoc_commit": "abc1234",
       "engines": {"docling": {"version": "2.130"}, "mineru": {"version": "4.0.7"}, "markitdown": {"version": "0.1.8"}},
       "fake": False,
       "missing": [{"sample": "nist-cswp-29-csf-2", "tier": "B", "why": "not fetched (bench/corpus/fetch.py)"}]}


def _run(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "results.jsonl").write_bytes((FIX / "bench_results_sample.jsonl").read_bytes())
    (run / "env.json").write_text(json.dumps(ENV), encoding="utf-8")
    return run


def test_report_matches_golden(tmp_path):
    run = _run(tmp_path)
    assert subprocess.run([sys.executable, str(BENCH / "report.py"), "--run", str(run)], check=False).returncode == 0
    text = (run / "summary.md").read_text(encoding="utf-8")
    assert text == (FIX / "bench_expected_summary.md").read_text(encoding="utf-8")
    assert "不可重現" in text and "不是 OmniDocBench" in text and "| vlm |" in text and "—" in text   # absent column
    assert text.count("**不宣稱的事**") == 4                       # under every table group (A, calibration, B, C)
    assert "markitdown/syn-scan-zh-01" in text and "nist-cswp-29-csf-2" in text   # failures and missing are listed


def test_readme_snippet_and_calibration_block(tmp_path):
    run = _run(tmp_path)
    (run / "calibration.json").write_text(json.dumps({
        "rho": 0.83, "precision": 0.75, "recall": 1.0, "n": 40, "min_rho": 0.6, "min_recall": 0.9, "passed": True,
        "defects": {"dropped": {"level": "warn", "exit_code": 0, "exit_code_fail_on_warn": 1, "flagged": [3, 6],
                                "expected": [3, 6]}}}), encoding="utf-8")
    snip = tmp_path / "snip.md"
    assert subprocess.run([sys.executable, str(BENCH / "report.py"), "--run", str(run), "--readme-snippet", str(snip),
                           "--link", "docs/benchmarks/2026-10-05.md"], check=False).returncode == 0
    s = snip.read_text(encoding="utf-8")
    assert "docs/benchmarks/2026-10-05.md" in s and "不是 OmniDocBench" in s and "繁中數位" in s
    text = (run / "summary.md").read_text(encoding="utf-8")
    assert "Spearman ρ" in text and "0.83" in text and "通過" in text


def test_small_corpus_rows_get_their_own_table(tmp_path):
    run = _run(tmp_path)
    extra = {"config": "vlm", "sample": "syn-text-zh", "tier": "A", "corpus": "A-small", "gt_level": "L1",
             "status": "low", "engine_used": "vlm", "seconds": 200.0, "pages": 3, "metrics": {"ned": 0.1},
             "verify": {"score": 0.3, "level": "low"}}
    with open(run / "results.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(extra) + "\n")
    subprocess.run([sys.executable, str(BENCH / "report.py"), "--run", str(run)], check=True)
    text = (run / "summary.md").read_text(encoding="utf-8")
    assert "Tier A 小樣本" in text and "繁中數位 ×2（L1）" in text        # the small sample is not mixed in
