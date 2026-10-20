"""Per-reason precision / recall of the per-page structural checks against the hand-judged real pages in
tests/fixtures/page_labels.json (verifier round 2, spec 2026-10-05 verify §2.3).

    uv run python bench/page_labels.py [--split test|dev|all]

The labelled PDFs and Markdown are private (tests/fixtures/manual/, never committed); each is checked by sha256.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LABELS = ROOT / "tests" / "fixtures" / "page_labels.json"
MANUAL = ROOT / "tests" / "fixtures" / "manual"
REASONS = ("page_dropped", "page_partial", "page_misaligned", "page_oversized")


class MissingSample(FileNotFoundError):
    pass


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def text_sha(pdf: Path) -> str:
    """sha256 of the PDF's raw text layers (pages joined by NUL): identifies a sample PDF that
    tests/fixtures/make_manual.py rebuilds from the original (its bytes differ on every save)."""
    import pymupdf
    with pymupdf.open(pdf) as d:
        return hashlib.sha256("\0".join(p.get_text() for p in d).encode("utf-8")).hexdigest()


def load(manual: Path = MANUAL) -> tuple[dict, list[dict]]:
    """(docs, labels); every PDF and Markdown is checked against the labelled identity first: the original's
    sha256 (`pdf_sha256`), or for a sample built from it the text-layer hash (`text_sha256`), and the judged
    Markdown's sha256."""
    data = json.loads(LABELS.read_text(encoding="utf-8"))
    for key, d in data["docs"].items():
        for kind in ("pdf", "md"):
            p = manual / d[kind]
            if not p.is_file():
                raise MissingSample(f"{key}: {p} is missing (private sample, see tests/fixtures/README.md)")
        pdf, md = manual / d["pdf"], manual / d["md"]
        ok_pdf = _sha(pdf) == d["pdf_sha256"] if "pdf_sha256" in d else text_sha(pdf) == d["text_sha256"]
        if not ok_pdf or _sha(md) != d["md_sha256"]:
            raise MissingSample(f"{key}: {pdf.name} / {md.name} do not match the labelled identity")
    return data["docs"], data["labels"]


def predictions(docs: dict, manual: Path = MANUAL) -> dict[str, dict[int, set[str]]]:
    """The conversion's page checks on each labelled document: {doc: {page: {reasons}}}."""
    from aidoc.pagemap import pdf_layers
    from aidoc.probe import probe_file
    from aidoc.quality import page_checks
    out: dict[str, dict[int, set[str]]] = {}
    for key, d in docs.items():
        pdf = manual / d["pdf"]
        md = (manual / d["md"]).read_text(encoding="utf-8")
        layers, content, body, untrusted = pdf_layers(pdf)
        pr = probe_file(pdf)
        out[key] = {}
        for e in page_checks(md, layers, content, pr.blank_pages, untrusted, body=body):
            out[key].setdefault(e["page"], set()).update(set(e["reasons"]) & set(REASONS))
    return out


def score(labels: list[dict], pred: dict[str, dict[int, set[str]]], split: str = "test") -> dict[str, dict]:
    rows = [r for r in labels if r["verdict"] != "ambiguous" and (split == "all" or r["split"] == split)]
    table = {}
    for reason in REASONS:
        tp = fp = fn = 0
        misses, false = [], []
        for r in rows:
            got = reason in pred.get(r["doc"], {}).get(r["page"], set())
            want = reason in r["expected"]
            tp += got and want
            if got and not want:
                fp += 1
                false.append(f"{r['doc']}:{r['page']}")
            if want and not got:
                fn += 1
                misses.append(f"{r['doc']}:{r['page']}")
        table[reason] = {"tp": tp, "fp": fp, "fn": fn, "support": tp + fn,
                         "precision": round(tp / (tp + fp), 3) if tp + fp else 1.0,
                         "recall": round(tp / (tp + fn), 3) if tp + fn else 1.0,
                         "false_positives": false, "misses": misses}
    return table


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test", choices=["test", "dev", "all"])
    a = ap.parse_args(argv)
    docs, labels = load()
    pred = predictions(docs)
    for reason, s in score(labels, pred, a.split).items():
        print(f"{reason:16} P={s['precision']:.3f} R={s['recall']:.3f} tp={s['tp']} fp={s['fp']} fn={s['fn']} "
              f"FP={s['false_positives']} FN={s['misses']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
