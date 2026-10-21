"""Real-sample evaluation rules (spec 2026-10-22 §7). The constants are pinned to the spec's literal numbers; a change
here is a spec change and voids the registered run."""
from __future__ import annotations

import copy
import json

import pytest

from bench.realeval import rules


def test_constants_match_spec():
    assert rules.SEED == 20261022
    assert rules.BOOTSTRAP_N == 10000
    assert rules.ROUTE_MIN_PAGES == 60
    assert rules.ROUTE_MIN_DOCS == 5
    assert rules.ROUTE_MARGIN == 0.05
    assert rules.ROUTE_MARGIN_LOW_GT == 0.10
    assert rules.GT_CER_SOFT == 0.025
    assert rules.GT_SPOT_PAGES == 12
    assert rules.ROUTE_DOC_WIN == 0.02
    assert rules.ROUTE_DOC_WINS_MIN == 5
    assert rules.ROUTE_DOC_BIG_LOSS == 0.05
    assert rules.FORMULA_MATCH == 0.80
    assert rules.FORMULA_MIN_DOCS == 3
    assert rules.FORMULA_MIN_DISPLAY == 15
    assert rules.FORMULA_RECOVERY_MIN == 0.50
    assert rules.FORMULA_GAP == 0.30
    assert rules.TEXT_GUARD == 0.05
    assert rules.VLM_COVER_PHRASE == "wouldn't it be dreamy"
    assert rules.VLM_RENDER_DPI == 200
    assert rules.VLM_MAX_LONG_SIDE_PX == 1400
    assert rules.VLM_MAX_TOKENS == 4096
    assert rules.VLM_PINNED == {
        "llamacpp_zip": "419de1292bcb772824fec45e4bc3722e79459e8d89c552578bafdad51266294a",
        "cudart_zip": "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668",
        "gguf": "f3ae46ec885050acf4b3d31944431e1fd90d50664fb09126af4a3c050ba14ee8",
        "mmproj": "204d757d7610d9b3faab10d506d69e5b244e32bf765e2bab2d0167e65e0a058a",
    }
    assert rules.CONTROL_RENDER_DPI == 300
    assert rules.CONTROL_VALIDITY_NED == 0.85
    assert rules.SNIPPET_MAX == 30
    assert rules.EPS == 1e-9          # tie tolerance only, far below the 4-decimal page scores
    assert rules.VLM_DOCS == 8            # spec §7.0: 6 zh + 2 en
    assert rules.VLM_LAYERS == 18         # spec §5.6: 18/18 layers on CUDA0


def test_vlm_thresholds_equal_rule_1():
    """VLM spec 2026-10-05 §3.4 rule 1: recall >= 0.95 on every scan, <= 10 s per page, peak VRAM <= 6 GB, cover
    phrase hit, reproducible install. Spec 2026-10-22 §7.3.1: these thresholds are not changed."""
    assert rules.VLM_RECALL_MIN == 0.95
    assert rules.VLM_MAX_SEC_PER_PAGE == 10.0
    assert rules.VLM_MAX_VRAM_MB == 6144


# ---------------------------------------------------------------- decide() (spec §7.1–§7.3)

GTQ = {"cer_upper": 0.01, "formula_gt_low_confidence": False}
VALID = {"checks": {"samples": True, "clean_tree": True, "control_ned": True, "auto_route": True, "vlm_setup": True}}


def _route(**kw):
    r = {"delta": 0.08, "lo": 0.02, "hi": 0.14, "n_pages": 89, "n_docs": 6, "n_docs_both": 6, "wins_D": 5,
         "wins_M": 0, "big_loss_D": 0, "big_loss_M": 0, "per_doc": {}}
    r.update(kw)
    return r


def _mirror(**kw):
    base = {"delta": -0.08, "lo": -0.14, "hi": -0.02, "wins_D": 0, "wins_M": 5}
    base.update(kw)
    return _route(**base)


def _papers(n_ok=3, r_m=0.7, r_d=0.0, t_m=0.9, t_d=0.9, hints=(True, True, True, True)):
    out = []
    for i in range(4):
        good = i < n_ok
        out.append({"doc": f"p{i}", "R_M": r_m if good else 0.1, "R_D": r_d if good else 0.1, "T_M": t_m, "T_D": t_d,
                    "n_display": 18 + i, "math_hint": hints[i],
                    "auto_reason": "pdf_math" if hints[i] else "pdf_default", "auto_engine": "mineru",
                    "both_ok": True})
    return out


def _vlm(recalls=None, **kw):
    recalls = recalls or [0.97] * 8
    v = {"per_doc": {f"d{i}": {"recall": r, "pages": 10, "max_sec": 4.0, "mean_sec": 1.2, "length_pages": 0,
                               "complete": True, "errors": 0} for i, r in enumerate(recalls)},
         "max_sec_overall": 4.7, "cover_hit": True, "cover_measured": True,
         "vram": {"baseline": 1000, "peak": 3400, "delta": 2400}, "load_seconds": 9.0, "launched": True,
         "pinned_ok": True, "server_cuda": True, "layers": "18/18", "nvidia_smi": True,
         "expected_docs": [f"d{i}" for i in range(8)]}
    v.update(kw)
    return v


def _decide(route=None, formulas=None, vlm=None, gtq=None, validity=None):
    return rules.decide(route if route is not None else _route(), formulas if formulas is not None else _papers(),
                        vlm if vlm is not None else _vlm(), gtq or GTQ, validity or VALID)


def test_margin_follows_the_gt_upper_bound():
    assert rules.margin_for(0.025) == 0.05 and rules.margin_for(0.0251) == 0.10
    assert _decide()["margin"] == 0.05


def test_zh_route_change():
    d = _decide()["zh_scan_route"]
    assert d["verdict"] == "change" and d["change_to"] == ["docling", "mineru"]
    assert d["conditions"]["delta"] == {"value": 0.08, "threshold": 0.05, "op": ">=", "ok": True}


@pytest.mark.parametrize(("kw", "failed"), [
    ({"delta": 0.04}, "delta"), ({"lo": -0.01}, "ci_lo"), ({"wins_D": 4}, "wins_D"),
    ({"big_loss_D": 1}, "big_loss_D"), ({"n_pages": 59}, "n_pages"), ({"n_docs_both": 4}, "n_docs_both"),
])
def test_zh_route_single_violation_is_insufficient(kw, failed):
    d = _decide(route=_route(**kw))["zh_scan_route"]
    assert d["verdict"] == "insufficient" and failed in d["failed"] and d["change_to"] is None
    assert d["needed_samples"]


def test_zh_route_confirm_is_the_mirror_image():
    d = _decide(route=_mirror())["zh_scan_route"]
    assert d["verdict"] == "confirm" and d["change_to"] is None and d["needed_samples"] == []
    for kw, failed in [({"hi": 0.001}, "ci_hi"), ({"wins_M": 4}, "wins_M"), ({"big_loss_M": 1}, "big_loss_M")]:
        d = _decide(route=_mirror(**kw))["zh_scan_route"]
        assert d["verdict"] == "insufficient" and failed in d["failed"]
    d = _decide(route=_mirror(delta=-0.04))["zh_scan_route"]
    assert d["verdict"] == "insufficient" and "neg_delta" in d["failed"]


def test_zh_route_margin_doubles_with_a_weak_gt():
    out = _decide(gtq={"cer_upper": 0.03, "formula_gt_low_confidence": False})
    d = out["zh_scan_route"]
    assert out["margin"] == 0.10 and d["verdict"] == "insufficient"
    assert d["conditions"]["delta"]["threshold"] == 0.10 and "delta" in d["failed"]


def test_zh_route_without_gt_quality_is_insufficient():
    out = _decide(gtq={"formula_gt_low_confidence": False})
    assert out["margin"] is None and out["zh_scan_route"]["verdict"] == "insufficient"
    assert "gt_quality" in out["zh_scan_route"]["failed"]


def test_zh_route_exact_threshold_counts():
    d = _decide(route=_route(delta=0.83 - 0.78))["zh_scan_route"]          # 0.04999999999999993
    assert d["verdict"] == "change"


def test_formula_route_confirm_without_detector_note():
    d = _decide()["formula_route"]
    assert d["verdict"] == "confirm" and d["detector_note"] is None
    assert d["detector_finding"].startswith("current rule holds")


def test_formula_route_confirm_with_a_detector_miss():
    d = _decide(formulas=_papers(hints=(True, False, True, True)))["formula_route"]
    assert d["verdict"] == "confirm" and "p1" in d["detector_note"] and "new samples" in d["detector_note"]


@pytest.mark.parametrize(("papers", "gtq", "failed"), [
    (_papers(n_ok=2), None, "papers_mineru"),
    (_papers(r_m=0.45), None, "papers_mineru"),
    (_papers(r_m=0.7, r_d=0.41), None, "papers_mineru"),
    (_papers(t_m=0.84, t_d=0.90), None, "papers_mineru"),
    (_papers(), {"cer_upper": 0.01, "formula_gt_low_confidence": True}, "formula_gt_confidence"),
])
def test_formula_route_insufficient(papers, gtq, failed):
    d = _decide(formulas=papers, gtq=gtq)["formula_route"]
    assert d["verdict"] == "insufficient" and failed in d["failed"]


def test_formula_route_gap_exactly_at_threshold_counts():
    d = _decide(formulas=_papers(r_m=0.7, r_d=0.4))["formula_route"]          # 0.7 − 0.4 = 0.2999999…
    assert d["verdict"] == "confirm"


def test_formula_route_needs_three_papers_both_converted():
    p = _papers(n_ok=4)
    for x in p[:2]:
        x["both_ok"] = False
    d = _decide(formulas=p)["formula_route"]
    assert d["verdict"] == "insufficient" and "n_papers" in d["failed"]


def test_formula_route_change_is_the_mirror_image():
    d = _decide(formulas=_papers(r_m=0.0, r_d=0.7))["formula_route"]
    assert d["verdict"] == "change" and d["change_to"] == ["docling", "mineru"]


def test_vlm_pass():
    d = _decide()["vlm_local"]
    assert d["verdict"] == "pass" and d["on_pass"].startswith("plan: VLM local backend 8a")
    assert "scanned_cht" in d["known_limitation"] and "§7.3.4" in d["known_limitation"]


@pytest.mark.parametrize(("vlm", "failed"), [
    (_vlm(recalls=[0.97] * 7 + [0.949]), "recall"),
    (_vlm(max_sec_overall=10.1), "max_sec"),
    (_vlm(vram={"baseline": 1000, "peak": 7145, "delta": 6145}), "vram"),
    (_vlm(cover_hit=False), "cover"),
])
def test_vlm_fail_names_the_condition(vlm, failed):
    d = _decide(vlm=vlm)["vlm_local"]
    assert d["verdict"] == "fail" and d["failed"] == [failed] and d["on_pass"] is None


@pytest.mark.parametrize(("vlm", "failed"), [
    (_vlm(pinned_ok=False), "pinned"),
    (_vlm(nvidia_smi=False), "vram_measured"),
    (_vlm(vram={"baseline": None, "peak": None, "delta": None}), "vram_measured"),
    (_vlm(launched=False), "vram_measured"),
    (_vlm(server_cuda=False), "on_gpu"),
    (_vlm(layers="17/18"), "on_gpu"),
    (_vlm(recalls=[0.97] * 7), "docs_measured"),
    (_vlm(cover_measured=False), "cover_measured"),
])
def test_vlm_insufficient_when_something_was_not_measured(vlm, failed):
    d = _decide(vlm=vlm)["vlm_local"]
    assert d["verdict"] == "insufficient" and failed in d["failed"]


def test_vlm_incomplete_document_is_insufficient():
    v = _vlm()
    v["per_doc"]["d3"]["complete"] = False
    d = _decide(vlm=v)["vlm_local"]
    assert d["verdict"] == "insufficient" and "docs_measured" in d["failed"]


def test_missing_parts_are_insufficient():
    out = rules.decide(None, None, None, GTQ, VALID)
    assert [out[k]["verdict"] for k in ("zh_scan_route", "formula_route", "vlm_local")] == ["insufficient"] * 3


def test_invalid_run_makes_every_verdict_insufficient():
    bad = {"checks": {**VALID["checks"], "control_ned": False}}
    out = _decide(validity=bad)
    assert out["validity_ok"] is False
    for k in ("zh_scan_route", "formula_route", "vlm_local"):
        assert out[k]["verdict"] == "insufficient" and out[k]["reason"] == "run invalid (validity checks)"


def test_decide_is_pure():
    args = (_route(), _papers(), _vlm(), dict(GTQ), copy.deepcopy(VALID))
    before = json.dumps(args, sort_keys=True)
    a = rules.decide(*args)
    b = rules.decide(*args)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert json.dumps(args, sort_keys=True) == before


def test_decisions_carry_the_route_input_for_hand_recomputation():
    d = _decide(route=_route(per_doc={"a": 0.03}))["zh_scan_route"]
    assert d["route"]["per_doc"] == {"a": 0.03} and d["route"]["hi"] == 0.14
