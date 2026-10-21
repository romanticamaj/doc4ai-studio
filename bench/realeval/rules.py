"""Pre-registered constants and decision rules of the real-sample evaluation (spec 2026-10-22 §7; the spec is the
authority — a number here that differs from it is a bug in this file). Changing a constant after the freeze commit
is a spec change and voids the run (spec §8.2)."""
from __future__ import annotations

SEED = 20261022
BOOTSTRAP_N = 10_000
ROUTE_MIN_PAGES = 60
ROUTE_MIN_DOCS = 5
ROUTE_MARGIN = 0.05            # spec §6.5: doubles to ROUTE_MARGIN_LOW_GT when the GT CER upper bound > GT_CER_SOFT
ROUTE_MARGIN_LOW_GT = 0.10
GT_CER_SOFT = 0.025
GT_SPOT_PAGES = 12             # 2 per zh document
ROUTE_DOC_WIN = 0.02
ROUTE_DOC_WINS_MIN = 5         # of 6
ROUTE_DOC_BIG_LOSS = 0.05
FORMULA_MATCH = 0.80
FORMULA_MIN_DOCS = 3           # of 4
FORMULA_MIN_DISPLAY = 15
FORMULA_RECOVERY_MIN = 0.50
FORMULA_GAP = 0.30
TEXT_GUARD = 0.05
VLM_RECALL_MIN = 0.95          # VLM spec §3.4 rule 1 — never changed here
VLM_MAX_SEC_PER_PAGE = 10.0
VLM_MAX_VRAM_MB = 6 * 1024
VLM_COVER_PHRASE = "wouldn't it be dreamy"
VLM_RENDER_DPI = 200
VLM_MAX_LONG_SIDE_PX = 1400
VLM_MAX_TOKENS = 4096
VLM_PINNED = {   # sha256 from docs/superpowers/spikes/2026-10-vlm-ocr.md
    "llamacpp_zip": "419de1292bcb772824fec45e4bc3722e79459e8d89c552578bafdad51266294a",
    "cudart_zip": "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668",
    "gguf": "f3ae46ec885050acf4b3d31944431e1fd90d50664fb09126af4a3c050ba14ee8",
    "mmproj": "204d757d7610d9b3faab10d506d69e5b244e32bf765e2bab2d0167e65e0a058a",
}
CONTROL_RENDER_DPI = 300
CONTROL_VALIDITY_NED = 0.85    # spec §7.0 check 3 on en-aashto-usrn-1984
SNIPPET_MAX = 30
EPS = 1e-9                     # floating-point tolerance at a threshold (0.83 - 0.80 = 0.02999...); not a rule

VLM_DOCS = 8                   # spec §7.0 / §7.3.1: 6 zh + 2 en documents, every one measured
VLM_LAYERS = 18                # spec §5.6: all 18 layers on CUDA0

ROUTE_CURRENT = ["mineru", "docling"]
ROUTE_CANDIDATE = ["docling", "mineru"]
RUN_INVALID = "run invalid (validity checks)"
ON_PASS = "plan: VLM local backend 8a (spec §7.3.3)"
KNOWN_LIMITATION = "scanned_cht repeated paragraph 0.75 (spec §7.3.4)"
NEEDED = {   # spec §11, for an `insufficient` verdict
    "zh_scan_route": [(">= 6 more Traditional Chinese scans, >= 60 pages, different sources: camera photos, ruled "
                       "tables, modern horizontal print, zh/en mixed; Wikisource level 4 preferred"),
                      "image route: >= 20 single-page PNG/JPG with GT"],
    "formula_route": [(">= 4 more CC BY arXiv papers with hand-downloaded e-prints compiled with page markers "
                       "(per-page formula and text GT)"),
                      ("detector: >= 10 non-TeX formula PDFs (Word/LibreOffice equation editor, matplotlib/plotly) "
                       "with source GT")],
    "vlm_local": [">= 20 pages / >= 5 real documents with repeated content",
                  ">= 3 more English scans (incl. handwritten notes)",
                  "one Linux/WSL2 measurement for local_supported"],
}


def margin_for(cer_upper: float | None) -> float | None:
    """Spec §6.5: 0.05 when the GT CER upper bound <= 0.025, else 0.10 (None when there is no GT quality file)."""
    if cer_upper is None:
        return None
    return ROUTE_MARGIN if cer_upper <= GT_CER_SOFT + EPS else ROUTE_MARGIN_LOW_GT


def _c(value, threshold, op: str) -> dict:
    if value is None or threshold is None:
        ok = False
    elif op == ">=":
        ok = value >= threshold - EPS
    elif op == "<=":
        ok = value <= threshold + EPS
    elif op == ">":
        ok = value > threshold + EPS
    elif op == "<":
        ok = value < threshold - EPS
    elif op == "==":
        ok = value == threshold
    else:
        raise ValueError(op)
    return {"value": value, "threshold": threshold, "op": op, "ok": bool(ok)}


def _failed(conds: dict, names) -> list[str]:
    return [n for n in names if not conds[n]["ok"]]


def _route_conditions(route: dict, margin: float | None) -> dict:
    d = route["delta"]
    return {
        "gt_quality": _c(margin is not None, True, "=="),
        "n_docs_both": _c(route["n_docs_both"], ROUTE_MIN_DOCS, ">="),
        "n_pages": _c(route["n_pages"], ROUTE_MIN_PAGES, ">="),
        "delta": _c(d, margin, ">="), "ci_lo": _c(route["lo"], 0.0, ">"),
        "wins_D": _c(route["wins_D"], ROUTE_DOC_WINS_MIN, ">="), "big_loss_D": _c(route["big_loss_D"], 0, "=="),
        "neg_delta": _c(-d, margin, ">="), "ci_hi": _c(route["hi"], 0.0, "<"),
        "wins_M": _c(route["wins_M"], ROUTE_DOC_WINS_MIN, ">="), "big_loss_M": _c(route["big_loss_M"], 0, "=="),
    }


_ROUTE_MIN = ("gt_quality", "n_docs_both", "n_pages")
_ROUTE_CHANGE = ("delta", "ci_lo", "wins_D", "big_loss_D")
_ROUTE_CONFIRM = ("neg_delta", "ci_hi", "wins_M", "big_loss_M")


def _route_change(c: dict) -> bool:
    return not _failed(c, _ROUTE_MIN + _ROUTE_CHANGE)


def _route_confirm(c: dict) -> bool:
    return not _failed(c, _ROUTE_MIN + _ROUTE_CONFIRM)


def _decide_route(route: dict | None, margin: float | None) -> dict:
    """Spec §7.1 (router._rule pdf_scanned, lang cht): current [mineru, docling], candidate [docling, mineru]."""
    if route is None:
        return {"verdict": "insufficient", "change_to": None, "conditions": {}, "failed": ["no_data"],
                "needed_samples": NEEDED["zh_scan_route"], "current": ROUTE_CURRENT}
    c = _route_conditions(route, margin)
    if _route_change(c):
        verdict, change_to, failed = "change", ROUTE_CANDIDATE, []
    elif _route_confirm(c):
        verdict, change_to, failed = "confirm", None, []
    else:
        side = _ROUTE_CHANGE if route["delta"] >= 0 else _ROUTE_CONFIRM
        verdict, change_to, failed = "insufficient", None, _failed(c, _ROUTE_MIN + side)
    return {"verdict": verdict, "change_to": change_to, "conditions": c, "failed": failed,
            "needed_samples": NEEDED["zh_scan_route"] if verdict == "insufficient" else [],
            "current": ROUTE_CURRENT}


def _formula_side(p: dict, mine: str, other: str) -> dict:
    """One paper's three conditions for engine `mine` (M or D) over `other` (spec §7.2)."""
    r_m, r_o, t_m, t_o = p[f"R_{mine}"], p[f"R_{other}"], p[f"T_{mine}"], p[f"T_{other}"]
    return {"recovery": _c(r_m, FORMULA_RECOVERY_MIN, ">="), "gap": _c(r_m - r_o, FORMULA_GAP, ">="),
            "text_guard": _c(t_m, t_o - TEXT_GUARD, ">=")}


def _decide_formulas(formulas: list[dict] | None, gt_quality: dict) -> dict:
    """Spec §7.2 (router._rule pdf_math): confirm / change need >= 3 eligible papers on one side."""
    if not formulas:
        return {"verdict": "insufficient", "change_to": None, "conditions": {}, "failed": ["no_data"],
                "detector_note": None, "detector_finding": None, "papers": {},
                "needed_samples": NEEDED["formula_route"]}
    eligible = [p["doc"] for p in formulas
                if p.get("both_ok") and (p.get("n_display") or 0) >= FORMULA_MIN_DISPLAY]
    papers = {p["doc"]: {"M": _formula_side(p, "M", "D"), "D": _formula_side(p, "D", "M"),
                         "eligible": p["doc"] in eligible} for p in formulas}
    n_m = sum(1 for d in eligible if all(x["ok"] for x in papers[d]["M"].values()))
    n_d = sum(1 for d in eligible if all(x["ok"] for x in papers[d]["D"].values()))
    c = {"formula_gt_confidence": _c(not gt_quality.get("formula_gt_low_confidence", True), True, "=="),
         "n_papers": _c(len(eligible), FORMULA_MIN_DOCS, ">="),
         "papers_mineru": _c(n_m, FORMULA_MIN_DOCS, ">="), "papers_docling": _c(n_d, FORMULA_MIN_DOCS, ">=")}
    base = ("formula_gt_confidence", "n_papers")
    detector_note = detector_finding = None
    if not _failed(c, (*base, "papers_mineru")):
        verdict, change_to, failed = "confirm", None, []
        missed = [p["doc"] for p in formulas if not p.get("math_hint")]
        not_routed = [p["doc"] for p in formulas if p.get("math_hint") and p.get("auto_reason") != "pdf_math"]
        if missed:
            detector_note = (f"detector missed: math_hint false on {', '.join(missed)}; a detector fix needs new "
                             "samples (spec §11); this evaluation does not change probe._is_math")
        elif not_routed:
            detector_note = f"math_hint true but auto did not route pdf_math on {', '.join(not_routed)}"
        else:
            detector_finding = "current rule holds on real TeX papers (math_hint true, auto routed pdf_math)"
    elif not _failed(c, (*base, "papers_docling")):
        verdict, change_to, failed = "change", ROUTE_CANDIDATE, []
    else:
        side = "papers_docling" if n_d > n_m else "papers_mineru"
        verdict, change_to, failed = "insufficient", None, _failed(c, (*base, side))
    return {"verdict": verdict, "change_to": change_to, "conditions": c, "failed": failed, "papers": papers,
            "detector_note": detector_note, "detector_finding": detector_finding,
            "needed_samples": NEEDED["formula_route"] if verdict == "insufficient" else []}


def _vlm_measured(v: dict) -> dict:
    expected = list(v.get("expected_docs") or [])
    complete = [n for n, d in (v.get("per_doc") or {}).items() if d.get("complete")]
    n_ok = sum(1 for n in expected if n in complete)
    vram = v.get("vram") or {}
    vram_ok = (bool(v.get("nvidia_smi")) and vram.get("delta") is not None and vram.get("baseline") is not None
               and bool(v.get("launched") or v.get("baseline_pre_server")))
    on_gpu = bool(v.get("server_cuda")) and v.get("layers") == f"{VLM_LAYERS}/{VLM_LAYERS}"
    return {"pinned": _c(bool(v.get("pinned_ok")), True, "=="),
            "on_gpu": _c(on_gpu, True, "=="),
            "vram_measured": _c(vram_ok, True, "=="),
            "docs_measured": {**_c(n_ok, VLM_DOCS, ">="), "expected": expected},
            "cover_measured": _c(bool(v.get("cover_measured")), True, "==")}


def _vlm_pass(v: dict) -> dict:
    recalls = {n: d["recall"] for n, d in (v.get("per_doc") or {}).items()}
    worst = min(recalls.values()) if recalls else None
    return {"recall": {**_c(worst, VLM_RECALL_MIN, ">="), "per_doc": recalls},
            "max_sec": _c(v.get("max_sec_overall"), VLM_MAX_SEC_PER_PAGE, "<="),
            "vram": _c((v.get("vram") or {}).get("delta"), VLM_MAX_VRAM_MB, "<="),
            "cover": _c(bool(v.get("cover_hit")), True, "==")}


def _decide_vlm(v: dict | None) -> dict:
    """Spec §7.3: pass = all five criteria met; fail = all measured, one missed; insufficient = something unmeasured."""
    out = {"on_pass": None, "known_limitation": KNOWN_LIMITATION}
    if v is None:
        return {**out, "verdict": "insufficient", "conditions": {}, "failed": ["no_data"],
                "needed_samples": NEEDED["vlm_local"]}
    m = _vlm_measured(v)
    p = _vlm_pass(v)
    conds = {**m, **p}
    missing = _failed(m, m)
    if missing:
        return {**out, "verdict": "insufficient", "conditions": conds, "failed": missing,
                "needed_samples": NEEDED["vlm_local"]}
    failed = _failed(p, p)
    if failed:
        return {**out, "verdict": "fail", "conditions": conds, "failed": failed, "needed_samples": []}
    return {**out, "verdict": "pass", "conditions": conds, "failed": [], "on_pass": ON_PASS, "needed_samples": []}


def decide(route: dict | None, formulas: list[dict] | None, vlm: dict | None, gt_quality: dict,
           validity: dict) -> dict:
    """The pre-registered rules of spec §7 as a pure function: inputs -> decisions.json (every input value and
    boolean appears in the output so the verifier can recompute by hand). No file access, inputs not mutated."""
    import copy
    route, formulas, vlm = copy.deepcopy(route), copy.deepcopy(formulas), copy.deepcopy(vlm)
    gt_quality = dict(gt_quality or {})
    checks = dict((validity or {}).get("checks") or {})
    valid = bool(checks) and all(checks.values())
    margin = margin_for(gt_quality.get("cer_upper"))
    out = {"margin": margin, "cer_upper": gt_quality.get("cer_upper"), "validity_ok": valid,
           "validity_checks": checks,
           "zh_scan_route": _decide_route(route, margin),
           "formula_route": _decide_formulas(formulas, gt_quality),
           "vlm_local": _decide_vlm(vlm)}
    if not valid:
        for k in ("zh_scan_route", "formula_route", "vlm_local"):
            out[k].update(verdict="insufficient", reason=RUN_INVALID, on_pass=None, change_to=None)
    return out
