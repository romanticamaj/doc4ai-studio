"""Real-sample evaluation rules (spec 2026-10-22 §7). The constants are pinned to the spec's literal numbers; a change
here is a spec change and voids the registered run."""
from __future__ import annotations

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


def test_vlm_thresholds_equal_rule_1():
    """VLM spec 2026-10-05 §3.4 rule 1: recall >= 0.95 on every scan, <= 10 s per page, peak VRAM <= 6 GB, cover
    phrase hit, reproducible install. Spec 2026-10-22 §7.3.1: these thresholds are not changed."""
    assert rules.VLM_RECALL_MIN == 0.95
    assert rules.VLM_MAX_SEC_PER_PAGE == 10.0
    assert rules.VLM_MAX_VRAM_MB == 6144
