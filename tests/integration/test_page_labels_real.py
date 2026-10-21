"""Per-reason precision / recall of the page checks on hand-judged real pages (verifier rounds 2-3, spec 2026-10-05
verify §2.3). Run: AIDOC_REQUIRE_MANUAL=1 uv run pytest -m slow tests/integration/test_page_labels_real.py -s

The gate is the held-out split only (`heldout_v2`, labelled blind by the independent verifier after the thresholds
were frozen): Wilson 95 % lower bound of precision >= 0.8 for every reason with flags, and of recall >= 0.8 for every
reason with >= 10 positives. A reason with fewer than 15 held-out positives is a strict xfail ("blind P/R not
measurable"): reported as an expected failure, never as a pass. The design split (`dev`) and all labels are printed as information. Missing samples,
and an unlabelled held-out split, skip normally and FAIL under AIDOC_REQUIRE_MANUAL=1."""
import os

import pytest

from bench import page_labels as PL

pytestmark = pytest.mark.slow


def _missing(msg: str):
    pytest.fail(msg) if os.environ.get("AIDOC_REQUIRE_MANUAL") == "1" else pytest.skip(msg)


@pytest.fixture(scope="module")
def scored():
    try:
        docs, labels = PL.load(PL.Path(os.environ.get("AIDOC_MANUAL_DIR") or PL.MANUAL))
    except PL.MissingSample as e:
        _missing(f"labelled sample missing: {e} — run: uv run python tests/fixtures/make_manual.py")
    pred = PL.predictions(docs)
    held = [r for r in labels if r["split"] == PL.HELDOUT and r["verdict"] != "ambiguous"]
    return {"n_heldout": len(held),
            **{split: PL.score(labels, pred, split) for split in (PL.HELDOUT, "dev", "all")}}


MEASURABLE_POS = 15            # held-out positives a reason needs before its blind P / R is measured


@pytest.mark.parametrize("reason", PL.REASONS)
def test_page_check_lower_bounds_on_the_heldout_split(scored, reason, request):
    for split in ("dev", "all", PL.HELDOUT):          # information: printed with -s and on failure
        s = scored[split][reason]
        print(f"{split:10} {reason:16} P={s['precision']:.3f} LB={s['precision_lb']} R={s['recall']:.3f} "
              f"LB={s['recall_lb']} support={s['support']} n={s['n']} FP={s['false_positives']} FN={s['misses']}")
    if scored["n_heldout"] == 0:
        _missing(f"split {PL.HELDOUT} has no labels yet: the independent verifier labels it blind "
                 "(protocol in tests/fixtures/page_labels.json 'splits')")
    s = scored[PL.HELDOUT][reason]
    if s["support"] < MEASURABLE_POS:
        # too few blind positives to measure: an expected failure, reported as xfail, never as a pass (strict: if
        # this ever passes, the marker must go and the bounds below apply)
        why = f"blind P/R not measurable: n_pos={s['support']} < {MEASURABLE_POS}"
        request.applymarker(pytest.mark.xfail(strict=True, reason=why))
        assert s["support"] >= MEASURABLE_POS, why
    if s["tp"] + s["fp"]:
        assert s["precision_lb"] >= PL.LB_MIN, (reason, s)
    if s["support"] >= PL.RECALL_MIN_POS:
        assert s["recall_lb"] >= PL.LB_MIN, (reason, s)
