import json
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2] / "bench"


def test_fake_run_produces_results_env_and_summary(tmp_path):
    out = tmp_path / "run"
    cmd = [sys.executable, str(BENCH / "run.py"), "--corpus", "A-small", "--configs", "auto,docling", "--out", str(out),
           "--fake"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    assert r.returncode == 0, r.stderr[-3000:] + r.stdout[-3000:]
    rows = [json.loads(line) for line in (out / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {row["config"] for row in rows} == {"auto", "docling"}
    assert len({row["sample"] for row in rows}) == 11                       # 12 small items minus the negatives
    assert all("ned" in row["metrics"] for row in rows if row["gt_level"] == "L1")
    assert all(row["status"] in ("done", "low", "warn", "failed") for row in rows)
    assert all(row["verify"]["level"] in ("ok", "warn", "low") for row in rows if row["status"] != "failed")
    env = json.loads((out / "env.json").read_text(encoding="utf-8"))
    assert "aidoc_commit" in env and "engines" in env and env["fake"] is True
    assert env["no_repair_configs"] == ["docling"]
    text = (out / "summary.md").read_text(encoding="utf-8")
    assert "假引擎" in text and "不是 OmniDocBench" in text and "| vlm |" in text
    # resume: a second run adds nothing
    r2 = subprocess.run(cmd + ["--resume"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                        check=False)
    assert r2.returncode == 0 and len((out / "results.jsonl").read_text(encoding="utf-8").splitlines()) == len(rows)
    # never the user's library: each config has its own data dir
    assert (out / "data-auto" / "aidoc.db").is_file() and (out / "data-docling" / "aidoc.db").is_file()
    r3 = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    assert r3.returncode != 0 and "--resume" in r3.stderr                     # never appends to a run by accident


def test_single_engine_configs_disable_repair():
    from bench.run import env_for_config
    assert env_for_config("docling")["AIDOC_BENCH_NO_REPAIR"] == "1"
    assert env_for_config("auto-norepair")["AIDOC_BENCH_NO_REPAIR"] == "1"
    assert "AIDOC_BENCH_NO_REPAIR" not in env_for_config("auto")


def test_vlm_config_refuses_a_remote_endpoint(tmp_path):
    r = subprocess.run([sys.executable, str(BENCH / "run.py"), "--corpus", "A-small", "--configs", "vlm", "--out",
                        str(tmp_path / "x"), "--vlm-base-url", "https://api.example.com/v1"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    assert r.returncode != 0 and "loopback" in r.stderr


def test_gpu_sampler_tracks_peak_and_delta():
    from bench.gpu_sampler import Sampler
    vals = iter([100, 150, 400, 120] + [120] * 100)
    with Sampler(interval=0.01, query=lambda: next(vals)) as s:
        import time
        time.sleep(0.2)
    assert s.baseline == 100 and s.peak == 400 and s.delta == 300
    with Sampler(interval=0.01, query=lambda: None) as s2:
        pass
    assert s2.peak is None and s2.delta is None


def test_tier_c_sample_without_gt_takes_its_page_count_from_the_pdf():
    from bench.run import Sample, _timeout
    s = Sample("x", "C", Path(__file__).resolve().parents[1] / "fixtures" / "big.pdf", None, "C")
    assert s.pages == 45 and _timeout(s) >= 45 * 30
