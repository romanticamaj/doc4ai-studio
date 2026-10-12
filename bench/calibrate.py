"""Calibration gate: does `aidoc verify` track the ground truth? (spec 2026-10-05 verify §3.4 last row, §7 #9)

    uv run python bench/calibrate.py --run bench/results/<run>/

Over the Tier A L1 rows of a run: Spearman ρ between `verify.score` and the mean page NED, and page-level
precision / recall of the pages verify flags (page_dropped / broken_text_layer / garbage / missing / failed) against
the pages that really are bad in that output (NED < 0.5, or a defect page — broken text layer, dropped — that the
output did not recover). Plus the four verify negatives of `syn-dropped-pages` (control, dropped, shifted, swapped).
Writes `calibration.json`; exit 1 when ρ < 0.6, recall < 0.9 or a negative is judged wrongly — the benchmark is not
published then.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from bench.metrics import spearman

CALIBRATION_MIN_RHO = 0.6
CALIBRATION_MIN_RECALL = 0.9
EXPECTED = {"control": ("ok", 0), "dropped": ("warn", 0), "shifted": ("low", 1), "swapped": ("low", 1)}


def expected(judged_gt: dict) -> dict:
    """Dropped pages are page-level warn, but like any flagged page more than 20 % of the content pages make the
    document low (spec 2026-10-01 §5.2): the small corpus drops 1 of 3 pages."""
    from aidoc.quality import FLAGGED_MAX_RATIO
    exp = dict(EXPECTED)
    content = judged_gt["pages"] - len(judged_gt.get("blank_pages") or [])
    if len(judged_gt["dropped_pages"]) > FLAGGED_MAX_RATIO * max(1, content):
        exp["dropped"] = ("low", 1)
    return exp


def _bad_pages(row: dict) -> set[int]:
    defects = row.get("gt_defects_unrecovered")
    if defects is None:
        defects = row.get("gt_defects") or []
    return set(row.get("low_pages") or []) | set(defects)


def _all_pages(row: dict) -> set[int]:
    return set(range(1, int(row.get("pages") or 0) + 1))


def calibrate_rows(rows: list[dict], *, low_flags_all: bool = False, defects_only: bool = False,
                   text_layer_only: bool = False, verifiable_only: bool = False, all_reasons: bool = False) -> dict:
    """The gate's numbers (defaults = the plan's definition). Diagnostic variants: `low_flags_all` counts every page
    of a document verify judged `low` as flagged; `defects_only` uses spec §3.4's GT defect pages (unrecovered) as the
    bad pages, without the NED < 0.5 pages; `text_layer_only` leaves out the scans (no text layer: verify cannot
    see OCR errors by design, spec §2.7)."""
    rows = [r for r in rows if r.get("tier") == "A" and r.get("gt_level") == "L1"
            and (r.get("metrics") or {}).get("ned") is not None and (r.get("verify") or {}).get("score") is not None
            and not (text_layer_only and r.get("kind") == "scan")
            and not (verifiable_only and r["verify"].get("verifiable") is not True)]
    if len(rows) < 2:
        return {"n": len(rows), "rho": None, "precision": None, "recall": None}
    rho = spearman([r["verify"]["score"] for r in rows], [r["metrics"]["ned"] for r in rows])
    tp = fp = fn = 0
    for r in rows:
        pred = set(r["verify"].get("flagged_pages") or [])
        if all_reasons:                            # every page verify flags, page_partial / misaligned / oversized too
            pred = set(r["verify"].get("flagged_all", r["verify"].get("flagged_pages")) or [])
        if low_flags_all and r["verify"].get("level") == "low":
            pred = _all_pages(r)
        bad = _bad_pages(r)
        if defects_only:
            d = r.get("gt_defects_unrecovered")
            bad = set(d if d is not None else (r.get("gt_defects") or []))
        tp += len(pred & bad)
        fp += len(pred - bad)
        fn += len(bad - pred)
    return {"n": len(rows), "rho": round(rho, 4), "precision": round(tp / (tp + fp), 4) if tp + fp else 1.0,
            "recall": round(tp / (tp + fn), 4) if tp + fn else 1.0, "tp": tp, "fp": fp, "fn": fn}


def fill_unrecovered(rows: list[dict], run: Path, corpus_root: Path) -> None:
    """Rows written before `gt_defects_unrecovered` existed: recompute it from the output on disk."""
    from aidoc.pagemap import split_pages
    from bench.metrics import RECOVERED_NED, page_scores
    for r in rows:
        if "gt_defects_unrecovered" in r or not r.get("gt_defects"):
            continue
        md = run / f"out-{r['config']}" / r["sample"] / f"{r['sample']}.md"
        gt_path = corpus_root / ("small" if r.get("corpus") == "A-small" else "") / r["sample"] / "gt.json"
        if not md.is_file() or not gt_path.is_file():
            continue
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        sc = page_scores(gt, split_pages(md.read_text(encoding="utf-8"))[1])
        r["gt_defects_unrecovered"] = [p for p in r["gt_defects"] if sc.get(p, {"ned": 0})["ned"] < RECOVERED_NED]


def gate(out: dict, min_rho: float = CALIBRATION_MIN_RHO, min_recall: float = CALIBRATION_MIN_RECALL) -> bool:
    return (out.get("rho") is not None and out["rho"] >= min_rho and out.get("recall") is not None
            and out["recall"] >= min_recall)


def judge_defect_markdowns(corpus_dir: Path) -> dict:
    """verify on the four negatives of `<corpus_dir>/syn-dropped-pages` against their base PDF."""
    from aidoc.verify import exit_code, verify_markdown
    d = Path(corpus_dir) / "syn-dropped-pages"
    gt = json.loads((d / "gt.json").read_text(encoding="utf-8"))
    pdf = (d / gt["pdf"]).resolve()
    base = json.loads((d / gt["pdf"]).parent.joinpath("gt.json").read_text(encoding="utf-8"))
    out: dict = {"_gt": {"pages": gt["pages"], "blank_pages": base.get("blank_pages") or [],
                         "dropped_pages": gt["defects"]["dropped_pages"]}}
    names = ["control", "dropped", "shifted", "swapped"] + [x for x in EXTRA if (d / f"{x}.md").is_file()]
    for name in names:
        rep = verify_markdown((d / f"{name}.md").read_text(encoding="utf-8"), pdf, sample=0)
        flagged = sorted(e["page"] for e in rep.pages if e.get("reasons"))
        expected = {"dropped": gt["defects"]["dropped_pages"], "shifted": [], "swapped": gt["defects"]["swapped_pages"],
                    "control": [], "swapped_content": gt["defects"].get("swapped_content_pages") or [],
                    "partial": gt["defects"].get("partial_pages") or []}[name]
        out[name] = {"level": rep.level, "reasons": rep.summary["reasons"], "exit_code": exit_code(rep, "low"),
                     "exit_code_fail_on_warn": exit_code(rep, "warn"), "flagged": flagged, "expected": expected}
    return out


EXTRA = ("swapped_content", "partial")       # verifier round; reported next to the gate, not part of it


def extra_negatives_ok(judged: dict) -> bool | None:
    """Swapped content under valid markers and a cut page: every expected page flagged, exit 1 with --fail-on warn."""
    got = [judged[x] for x in EXTRA if x in judged]
    if not got:
        return None
    return all(j["exit_code_fail_on_warn"] == 1 and set(j["expected"]) <= set(j["flagged"]) for j in got)


def defects_ok(judged: dict) -> bool:
    ok = True
    for name, (level, code) in expected(judged["_gt"]).items():
        j = judged.get(name) or {}
        ok &= j.get("level") == level and j.get("exit_code") == code
    d = judged["dropped"]
    ok &= d["exit_code_fail_on_warn"] == 1 and set(d["expected"]) <= set(d["flagged"])
    return bool(ok)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", required=True)
    ap.add_argument("--corpus", default=None, help="corpus dir holding syn-dropped-pages (default: full, else small)")
    a = ap.parse_args(argv)
    run = Path(a.run)
    rows = [json.loads(x) for x in (run / "results.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    fill_unrecovered(rows, run, HERE / "corpus")
    out = calibrate_rows(rows)
    corpus = Path(a.corpus) if a.corpus else (HERE / "corpus" if (HERE / "corpus" / "syn-dropped-pages").is_dir()
                                              else HERE / "corpus" / "small")
    out["defects"] = judge_defect_markdowns(corpus)
    out["defects_corpus"] = str(corpus.relative_to(HERE.parent)) if corpus.is_relative_to(HERE.parent) else str(corpus)
    per_config = {}
    for c in sorted({r["config"] for r in rows}):
        per_config[c] = calibrate_rows([r for r in rows if r["config"] == c])
    out["per_config"] = per_config
    out["diagnostics"] = {name: calibrate_rows(rows, **kw) for name, kw in (
        ("low_document_flags_every_page", {"low_flags_all": True}),
        ("spec_defect_pages_only", {"defects_only": True}),
        ("text_layer_samples_only", {"text_layer_only": True}),
        ("verifiable_rows_only", {"verifiable_only": True}),
        ("all_page_reasons", {"all_reasons": True}),
        ("all_page_reasons_text_layer_samples", {"all_reasons": True, "text_layer_only": True}),
        ("all_three", {"low_flags_all": True, "defects_only": True, "text_layer_only": True}))}
    out.update(min_rho=CALIBRATION_MIN_RHO, min_recall=CALIBRATION_MIN_RECALL)
    out["passed"] = gate(out) and defects_ok(out["defects"])
    out["extra_negatives_ok"] = extra_negatives_ok(out["defects"])
    (run / "calibration.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"rho={out['rho']} precision={out['precision']} recall={out['recall']} n={out['n']} "
          f"defects_ok={defects_ok(out['defects'])} -> {'PASS' if out['passed'] else 'FAIL'}")
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
