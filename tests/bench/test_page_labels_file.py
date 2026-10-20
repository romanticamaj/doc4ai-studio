"""The committed label file holds no document text, and the scorer counts what it should (verifier round 2)."""
import json
from pathlib import Path

from bench.page_labels import LABELS, REASONS, score


def test_label_file_has_identities_and_verdicts_only():
    data = json.loads(Path(LABELS).read_text(encoding="utf-8"))
    for d in data["docs"].values():
        assert set(d) <= {"pdf_sha256", "source_sha256", "text_sha256", "md_sha256", "pdf", "md", "made_by"}
        assert ("pdf_sha256" in d) != ("text_sha256" in d) and len(d["md_sha256"]) == 64
    keys = set()
    for r in data["labels"]:
        assert set(r) <= {"doc", "page", "expected", "verdict", "split", "note"} and r["doc"] in data["docs"]
        assert set(r["expected"]) <= set(REASONS) and r["verdict"] in ("loss", "clean", "misplaced", "ambiguous")
        assert r["split"] in ("dev", "test") and (r["doc"], r["page"]) not in keys
        keys.add((r["doc"], r["page"]))
    assert len(keys) >= 60


def test_score_counts_precision_recall_and_skips_ambiguous():
    labels = [{"doc": "a", "page": 1, "expected": ["page_dropped"], "verdict": "loss", "split": "test"},
              {"doc": "a", "page": 2, "expected": [], "verdict": "clean", "split": "test"},
              {"doc": "a", "page": 3, "expected": [], "verdict": "ambiguous", "split": "test"},
              {"doc": "a", "page": 4, "expected": ["page_dropped"], "verdict": "loss", "split": "dev"}]
    pred = {"a": {1: {"page_dropped"}, 2: {"page_dropped"}, 3: {"page_dropped"}}}
    s = score(labels, pred, "test")["page_dropped"]
    assert (s["tp"], s["fp"], s["fn"], s["precision"], s["recall"]) == (1, 1, 0, 0.5, 1.0)
    assert score(labels, pred, "all")["page_dropped"]["misses"] == ["a:4"]
