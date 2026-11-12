# Benchmark

`bench/` (not part of the package) measures what the orchestration adds — automatic routing, fallback and per-page
repair — against each engine alone, on documents with ground truth:

- **Tier A**, synthetic and regenerated from pinned sources: Traditional Chinese and English text from Project
  Gutenberg typeset with the SIL OFL Noto Sans CJK TC font, degraded scans, PDFs whose fonts lack `/ToUnicode` (the
  Head First case), ruled tables, formulas, two columns, a 1,200-page book, and Markdown with deliberately dropped,
  shifted and swapped pages for calibrating `verify`.
- **Tier B**, real documents under open licences fetched by sha256 from `bench/corpus/MANIFEST.json`: Taiwan
  government gazettes (Open Government Data License), arXiv CC BY 4.0 papers, NIST publications.
- **Tier C**, two private books: reported separately and marked not reproducible.

```bash
uv run python bench/corpus/make_synthetic.py                 # Tier A (about 2 minutes; downloads the pinned sources)
uv run python bench/corpus/fetch.py                          # Tier B
uv run python bench/run.py --corpus A,B --configs auto,auto-norepair,docling,mineru,markitdown --out bench/results/<run>
uv run python bench/calibrate.py --run bench/results/<run>   # gate: verify must track the ground truth
uv run python bench/report.py --run bench/results/<run>      # summary.md
```

Single-engine columns and `auto-norepair` run with per-page repair turned off, so the comparison does not borrow
another engine's repair.

No results are published yet. Before a table goes here, `verify` must track the ground truth (Spearman ρ ≥ 0.6
between its score and the page text accuracy, page recall ≥ 0.9). The first run on 2026-10-12 did not pass
(ρ 0.48, recall 0.16 — precision was 1.00: the pages it flags are bad, but it misses most bad pages, mainly OCR
misreadings in scans and paragraphs moved to a neighbouring page), nor did the re-run on 2026-10-13 after the page-level checks were added (ρ 0.47, recall 0.05 under the unchanged gate definition).
On 2026-10-20 the user redefined the gate (rows and pages `verify` can judge; ρ ≥ 0.6, page precision and recall
≥ 0.9 over every page-level reason; scans reported separately) and it was run once on a fresh-seed Tier A plus
Tier B: ρ 0.63, precision 0.83, recall 0.72 — not passed, so still nothing is published.
A separate real-sample evaluation (6 Traditional Chinese scans, 4 arXiv papers, 2 English controls; pre-registered rules) ran once on 2026-10-22 and was **invalid**: the shared English-control check failed (NED 0.70 / 0.59 < 0.85), so all three decisions are `insufficient` and nothing changed: the zh scan route, the formula route, and the local VLM backend.
