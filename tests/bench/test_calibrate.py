import json
from pathlib import Path

from bench import calibrate as C

SMALL = Path(__file__).resolve().parents[2] / "bench" / "corpus" / "small"


def test_calibration_metrics_from_rows():
    rows = [{"tier": "A", "gt_level": "L1", "metrics": {"ned": 0.98}, "verify": {"score": 0.99, "flagged_pages": []},
             "gt_defects": [], "low_pages": []},
            {"tier": "A", "gt_level": "L1", "metrics": {"ned": 0.60}, "verify": {"score": 0.70, "flagged_pages": [3, 9]},
             "gt_defects": [3, 9], "low_pages": [3, 9]},
            {"tier": "A", "gt_level": "L1", "metrics": {"ned": 0.30}, "verify": {"score": 0.40, "flagged_pages": [1, 2]},
             "gt_defects": [1, 2, 5], "low_pages": [1, 2, 5]}]
    out = C.calibrate_rows(rows)
    assert out["rho"] > 0.9 and out["recall"] == 4 / 5 and out["precision"] == 1.0
    assert C.gate(out, min_rho=0.6, min_recall=0.9) is False and C.gate({**out, "recall": 0.95}, 0.6, 0.9) is True


def test_recovered_defect_pages_are_not_misses():
    """A broken-text-layer page that the output repaired is not a page verify should flag."""
    row = {"tier": "A", "gt_level": "L1", "metrics": {"ned": 0.99}, "verify": {"score": 0.99, "flagged_pages": []},
           "gt_defects": [8, 23], "gt_defects_unrecovered": [], "low_pages": []}
    out = C.calibrate_rows([row, {**row, "metrics": {"ned": 0.5}, "verify": {"score": 0.5, "flagged_pages": []}}])
    assert out["recall"] == 1.0 and out["fn"] == 0


def test_dropped_pages_fixtures_are_judged():
    res = C.judge_defect_markdowns(SMALL)             # {name: {"level", "exit_code", "flagged"}}
    gt = json.loads((SMALL / "syn-dropped-pages" / "gt.json").read_text(encoding="utf-8"))
    # 1 of 3 pages dropped (> 20 %): low like any flagged-page majority; the full corpus drops 5 of 29: warn
    assert res["dropped"]["level"] == "low" and set(res["dropped"]["flagged"]) >= set(gt["defects"]["dropped_pages"])
    assert res["dropped"]["exit_code"] == 1 and res["dropped"]["exit_code_fail_on_warn"] == 1
    assert C.expected(res["_gt"])["dropped"] == ("low", 1)
    assert C.expected({"pages": 30, "blank_pages": [15], "dropped_pages": [1, 2, 3, 4, 5]})["dropped"] == ("warn", 0)
    assert res["shifted"]["level"] == "low" and res["swapped"]["level"] == "low" and res["control"]["level"] == "ok"
    assert res["shifted"]["exit_code"] == 1 and res["swapped"]["exit_code"] == 1 and res["control"]["exit_code"] == 0
    assert C.defects_ok(res)
