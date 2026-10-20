"""Per-reason precision / recall of the page checks on the hand-judged real pages (verifier round 2, spec 2026-10-05
verify §2.3). Run: AIDOC_REQUIRE_MANUAL=1 uv run pytest -m slow tests/integration/test_page_labels_real.py

The label file holds only document identities, page numbers and verdicts; the PDFs and judged Markdown are private
samples (tests/fixtures/manual/). Missing samples skip normally and FAIL under AIDOC_REQUIRE_MANUAL=1."""
import os

import pytest

from bench import page_labels

pytestmark = pytest.mark.slow
MIN = 0.9


@pytest.fixture(scope="module")
def scored():
    try:
        docs, labels = page_labels.load(page_labels.Path(os.environ.get("AIDOC_MANUAL_DIR") or page_labels.MANUAL))
    except page_labels.MissingSample as e:
        msg = f"labelled sample missing: {e} — run: uv run python tests/fixtures/make_manual.py"
        pytest.fail(msg) if os.environ.get("AIDOC_REQUIRE_MANUAL") == "1" else pytest.skip(msg)
    pred = page_labels.predictions(docs)
    return {split: page_labels.score(labels, pred, split) for split in ("all", "test", "dev")}


@pytest.mark.parametrize("reason", page_labels.REASONS)
def test_page_check_precision_and_recall_on_labelled_pages(scored, reason):
    for split in ("test", "dev", "all"):          # printed with -s / on failure: every split, false positives, misses
        s = scored[split][reason]
        print(f"{split:4} {reason:16} P={s['precision']:.3f} R={s['recall']:.3f} support={s['support']} "
              f"FP={s['false_positives']} FN={s['misses']}")
    s = scored["all"][reason]
    assert s["support"] >= 3, f"{reason}: too few labelled positives to measure ({s['support']})"
    assert s["precision"] >= MIN and s["recall"] >= MIN, (
        f"{reason}: precision {s['precision']} / recall {s['recall']} on {s['support']} labelled positives "
        f"(false positives {s['false_positives']}, misses {s['misses']})")
