"""Page-aligned scoring of groups A and C, the paired page bootstrap and the route statistics of decision (1a)
(spec 2026-10-22 §4.2, §5.2, §5.3).

- Only GT pages are scored; a GT page with no section in the output is an empty prediction (NED 0); a failed
  conversion (`markdown is None`) scores 0 on every GT page and is flagged `failed`.
- PDF page N's section is compared with GT page N as a whole (the supreme-court spreads are not cut in half).
- Document level = page-weighted means. Page rows keep 4 decimals (as bench.metrics); the route aggregates are
  not rounded, so rounding can never move a value across a decision threshold."""
from __future__ import annotations

import random
from collections import Counter

from rapidfuzz.distance import Levenshtein

from aidoc.pagemap import split_pages
from bench.metrics import DROPPED_SECTION_MAX
from bench.realeval.normalize import norm_gt, norm_pred
from bench.realeval.rules import BOOTSTRAP_N, EPS, ROUTE_DOC_BIG_LOSS, ROUTE_DOC_WIN, SEED


def bag_prf(gt_sq: str, pred_sq: str) -> tuple[float, float, float]:
    """Character-multiset recall, precision, F1 (common = Σ_c min(n_gt(c), n_pred(c)))."""
    a, b = Counter(gt_sq), Counter(pred_sq)
    common = sum(min(n, b[c]) for c, n in a.items())
    r = common / len(gt_sq) if gt_sq else 0.0
    p = common / len(pred_sq) if pred_sq else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return r, p, f


def _ned(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return 1.0 - Levenshtein.normalized_distance(a, b)


def page_row(page: int, gt_text: str, pred_md: str, *, failed: bool = False) -> dict:
    g, p = norm_gt(gt_text), norm_pred(pred_md)
    r, pr, f = bag_prf(g, p)
    common = round(r * len(g))
    return {"page": page, "ned": round(_ned(g, p), 4),
            "cer": round(Levenshtein.distance(g, p) / len(g), 4) if g else (0.0 if not p else 1.0),
            "recall": round(r, 4), "precision": round(pr, 4), "f1": round(f, 4), "common": common,
            "dropped": bool(g) and len(p) < DROPPED_SECTION_MAX, "gt_len": len(g), "pred_len": len(p),
            "failed": failed}


def page_rows(sample, gt_pages: dict[int, str], markdown: str | None) -> list[dict]:
    """One row per GT page, in page order (`sample` is informational; GT text comes in with errata applied)."""
    if markdown is None:
        return [page_row(p, gt_pages[p], "", failed=True) for p in sorted(gt_pages)]
    _, secs = split_pages(markdown)
    return [page_row(p, gt_pages[p], secs.get(p, "")) for p in sorted(gt_pages)]


def _mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def doc_summary(rows: list[dict]) -> dict:
    by_ned = sorted(rows, key=lambda r: (r["ned"], r["page"]))
    return {"ned": _mean([r["ned"] for r in rows]), "cer": _mean([r["cer"] for r in rows]),
            "recall": _mean([r["recall"] for r in rows]), "precision": _mean([r["precision"] for r in rows]),
            "f1": _mean([r["f1"] for r in rows]), "n_pages": len(rows),
            "n_dropped": sum(1 for r in rows if r["dropped"]),
            "worst": {"page": by_ned[0]["page"], "ned": by_ned[0]["ned"]} if rows else None,
            "worst3": [{"page": r["page"], "ned": r["ned"]} for r in by_ned[:3]],
            "failed": any(r.get("failed") for r in rows)}


def _percentile(sorted_xs: list[float], q: float) -> float:
    """Linear interpolation between the closest ranks (q in [0, 1])."""
    k = q * (len(sorted_xs) - 1)
    lo = int(k)
    hi = min(lo + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (k - lo)


def paired_bootstrap(diffs: list[float], n: int = BOOTSTRAP_N, seed: int = SEED) -> tuple[float, float, float]:
    """(mean, lo, hi): pages resampled with replacement n times, random.Random(seed), percentiles 2.5 / 97.5."""
    k = len(diffs)
    if not k:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    means = sorted(sum(diffs[rng.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return sum(diffs) / k, _percentile(means, 0.025), _percentile(means, 0.975)


def route_stats(per_doc: dict[str, dict[str, list[dict]]]) -> dict:
    """per_doc[doc]["docling" | "mineru"] = page rows. Δ = mean over pages of NED_docling − NED_mineru."""
    diffs: list[float] = []
    deltas: dict[str, float] = {}
    both = 0
    for doc, cfg in per_doc.items():
        d_rows = {r["page"]: r for r in cfg.get("docling") or []}
        m_rows = {r["page"]: r for r in cfg.get("mineru") or []}
        pages = sorted(set(d_rows) & set(m_rows))
        if not pages:
            continue
        diffs += [d_rows[p]["ned"] - m_rows[p]["ned"] for p in pages]
        deltas[doc] = (sum(d_rows[p]["ned"] for p in pages) - sum(m_rows[p]["ned"] for p in pages)) / len(pages)
        if not any(r.get("failed") for r in (*d_rows.values(), *m_rows.values())):
            both += 1
    mean, lo, hi = paired_bootstrap(diffs)
    eps = EPS                                   # a tie at a threshold is decided as the spec writes it (≥ / ≤)
    return {"delta": mean, "lo": lo, "hi": hi, "n_pages": len(diffs),
            "n_docs": len(deltas), "n_docs_both": both,
            "wins_D": sum(1 for v in deltas.values() if v >= ROUTE_DOC_WIN - eps),
            "wins_M": sum(1 for v in deltas.values() if v <= -ROUTE_DOC_WIN + eps),
            "big_loss_D": sum(1 for v in deltas.values() if v <= -ROUTE_DOC_BIG_LOSS + eps),
            "big_loss_M": sum(1 for v in deltas.values() if v >= ROUTE_DOC_BIG_LOSS - eps),
            "per_doc": deltas}
