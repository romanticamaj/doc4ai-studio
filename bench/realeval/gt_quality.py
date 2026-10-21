"""Ground-truth quality (spec 2026-10-22 §6) — the verifier's tool, used before the freeze.

    uv run python bench/realeval/gt_quality.py --draw                       # the 12 seeded pages + 12 formula indices
    uv run python bench/realeval/gt_quality.py --summarise --root <realeval> --author <name>

--draw reads only the manifest (no sample file). The pages are drawn, never chosen: one random.Random(SEED) over the
zh documents in manifest order, sample(gt_pages, 2) each; the formula indices are a second random.Random(SEED),
sample(range(n_display), 3) per paper in manifest order (0-based positions in the paper's display-formula list).

--summarise reads <root>/gt_spotcheck.json [{doc, page, k, n}], gt_errata.json, alignment.json
[{doc, first_ok, last_ok, spread_order_ok?}] and formula_spotcheck.json [{doc, index, ok}] and writes
<root>/gt_quality.json: CER_gt = Σk / Σn, its Wilson 95 % upper bound, the margin of spec §6.5 and
formula_gt_low_confidence (>= 3 of 12 formulas not the printed one, spec §6.4)."""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from bench.realeval.rules import SEED, margin_for
from bench.realeval.samples import MANIFEST, REAL_ROOT

PAGES_PER_DOC = 2
FORMULAS_PER_PAPER = 3
FORMULA_LOW_CONF_MIN = 3        # spec §6.4: >= 3 of 12 not the formula → low confidence


def draw(manifest: list[dict], seed: int = SEED) -> dict[str, list[int]]:
    rng = random.Random(seed)
    return {e["name"]: rng.sample(list(e["gt_pages"]), PAGES_PER_DOC) for e in manifest if e["group"] == "A"}


def draw_formulas(manifest: list[dict], seed: int = SEED) -> dict[str, list[int]]:
    rng = random.Random(seed)
    return {e["name"]: rng.sample(range(int(e["n_display"])), FORMULAS_PER_PAPER)
            for e in manifest if e["group"] == "B"}


def wilson_upper(k: int, n: int, z: float = 1.96) -> float | None:
    """Wilson score interval upper bound (the symmetric form of bench.page_labels.wilson_lower; not rounded)."""
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c + h) / d


def margin(cer_upper: float) -> float:
    return margin_for(cer_upper)


def formula_low_confidence(checks: list[dict]) -> bool:
    return sum(1 for c in checks if not c.get("ok")) >= FORMULA_LOW_CONF_MIN


def _load(p: Path):
    if not p.is_file():
        raise SystemExit(f"{p.name} is missing")
    return json.loads(p.read_text(encoding="utf-8"))


def summarise(root: Path, *, manifest: Path | None = None, author: str) -> dict:
    root = Path(root)
    m = json.loads(Path(manifest or MANIFEST).read_text(encoding="utf-8"))
    spot = _load(root / "gt_spotcheck.json")
    errata = _load(root / "gt_errata.json")
    align = _load(root / "alignment.json")
    fspot = _load(root / "formula_spotcheck.json")
    want = {(d, p) for d, ps in draw(m).items() for p in ps}
    got = {(e["doc"], int(e["page"])) for e in spot}
    if got != want or len(spot) != len(want):
        raise SystemExit("gt_spotcheck.json must hold exactly the pages drawn by --draw (no page is chosen by hand)")
    fwant = {(d, i) for d, ix in draw_formulas(m).items() for i in ix}
    if {(e["doc"], int(e["index"])) for e in fspot} != fwant or len(fspot) != len(fwant):
        raise SystemExit("formula_spotcheck.json must hold exactly the formula indices drawn by --draw")
    k = sum(int(e["k"]) for e in spot)
    n = sum(int(e["n"]) for e in spot)
    up = wilson_upper(k, n)
    docs = {e["name"] for e in m if e["group"] in ("A", "C")}
    al_ok = {a["doc"] for a in align} == docs and all(
        a.get("first_ok") and a.get("last_ok") and a.get("spread_order_ok", True) for a in align)
    q = {"pages": spot, "k": k, "n": n, "cer": k / n if n else None, "cer_upper": up,
         "margin": margin_for(up), "formula_checks": fspot, "formula_gt_low_confidence": formula_low_confidence(fspot),
         "errata_count": len(errata), "alignment": align, "alignment_ok": al_ok, "author": author,
         "date": datetime.now().astimezone().isoformat(timespec="seconds"), "seed": SEED}
    (root / "gt_quality.json").write_text(json.dumps(q, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return q


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--draw", action="store_true")
    g.add_argument("--summarise", action="store_true")
    ap.add_argument("--root", default=str(REAL_ROOT))
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--author")
    a = ap.parse_args(argv)
    m = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    if a.draw:
        print(f"seed {SEED}; text spot-check pages (spec s6.3):")
        for doc, ps in draw(m).items():
            print(f"  {doc}: pages {', '.join(str(p) for p in sorted(ps))}")
        print("display-formula spot-check (spec s6.4; 0-based index into the paper's display == 'block' list):")
        for doc, ix in draw_formulas(m).items():
            print(f"  {doc}: indices {', '.join(str(i) for i in sorted(ix))}")
        return 0
    if not a.author:
        raise SystemExit("--summarise needs --author")
    q = summarise(Path(a.root), manifest=Path(a.manifest), author=a.author)
    print(f"k={q['k']} n={q['n']} CER_gt={q['cer']:.4f} upper={q['cer_upper']:.4f} margin={q['margin']} "
          f"formula_gt_low_confidence={q['formula_gt_low_confidence']} alignment_ok={q['alignment_ok']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
