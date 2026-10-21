# `aidoc verify` and Public Benchmark — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Read `docs/superpowers/plans/2026-09-24-index.md` (cross-phase contracts) and the spec first; Task 0 amends the index. Tasks 1–8 (verify) and Tasks 9–13 (benchmark) are independent until Task 14 (calibration) joins them. Task 14 is a gate: the benchmark is not published while `verify` fails its calibration.

**Goal:** (a) `aidoc verify` — one pure function (`aidoc.verify.verify_markdown`) that scores ANY Markdown against its source PDF with the existing per-page quality model, exposed as CLI (exit codes for CI), `POST /api/verify` and two MCP tools, understanding third-party page-marker dialects and inferring page boundaries when there are none; (b) a reproducible, legally redistributable benchmark (`bench/`) with synthetic + public-domain/open-licence corpora, ground truth, metrics, a runner comparing `auto` against single engines (including `vlm` when available), a fixed results table and a fixed "what we do not claim" text.

**Spec:** `docs/superpowers/specs/2026-10-05-verify-and-benchmark-design.md` (Traditional Chinese). Related: `2026-10-01-per-page-quality-and-page-map.md` (§5 model, §7 spot check), `2026-10-01-mcp-server-design.md` (§5 tool rules), `2026-10-05-vlm-ocr-engine-design.md` (the `vlm` benchmark column), index `2026-09-24-index.md`.

**Architecture:** `markers.py` (dialects → unified `<!-- page: N -->` view), `pagemap.infer_pages()` (unique-probe anchoring, monotone filter, boundary snap), `verify.py` (`VerifyReport`, per-page checks reusing `quality`/`pagemap`/`probe`, new `page_dropped` + advisories), `verify_report.py` (summary text, self-contained HTML), CLI `aidoc verify`, API `POST /api/verify`, MCP `verify_document`/`verify_markdown`. Benchmark lives outside the package: `bench/corpus/make_synthetic.py` (Tier A, Noto OFL fonts, Gutenberg zh-Hant text, Augraphy degradation, broken-ToUnicode variants, 1200-page book), `bench/corpus/fetch.py` + `MANIFEST.json` (Tier B with licence fields), `bench/metrics.py` (NED/CER, TEDS, formula NED, recovery, coverage), `bench/run.py` (configs, warm-up, GPU sampling, resumable `results.jsonl`), `bench/report.py` (fixed tables + non-claims text) → `docs/benchmarks/<date>.md` + README section.

**Tech Stack:** Python 3.10 host (PyMuPDF, rapidfuzz — added to the **dev/bench** dependency group only; `verify` itself must not need rapidfuzz: it uses the existing `pagemap`/`quality` code), Augraphy + Pillow + `img2pdf` (bench-only), optional `table_recognition_metric` or an in-repo APTED, FastAPI, MCP SDK, Vitest not needed (no web UI in v1).

## Global Constraints

Index §0 plus:
- `aidoc.verify.verify_markdown()` is pure: no `Store`, no file writes, no network, deterministic for the same inputs (seeded probes come from `pagemap`).
- `verify` never text-searches to *find* page boundaries in our own outputs (page markers rule, index A20) — `infer_pages()` is used only when a dialect yields no usable markers, and it anchors by **unique** probes exactly as spec §2.2.
- Nothing under `bench/corpus/` that is committed may carry a licence other than: our own generated files, Project Gutenberg public-domain text (header stripped), SIL OFL fonts (downloaded, not committed), Taiwan OGDL-1, CC BY/CC BY-SA/CC0, US-government public domain. `fetch.py` refuses a MANIFEST entry without `license` + `license_url`.
- `bench/` is not part of the `aidoc` wheel (hatch `packages = ["src/aidoc"]` already excludes it) and its GPU parts never run in CI; the `--fake` path does.
- Exit codes: `0` passed, `1` failed `--fail-on`, `3` unverifiable, `4` usage/input error — constants in `verify.py`.
- Constants: `DROPPED_LAYER_MIN = 120`, `DROPPED_SECTION_MAX = 20`, `INFER_WIN = 24`, `INFER_PROBES = 3`, `INFER_MIN_CONFIDENCE = 0.5` (below → `verifiable = false`), `HTML_THUMB_WIDTH = 160`, `HTML_MAX_THUMBS = 200`, `VERIFY_VERSION = 1`, `CALIBRATION_MIN_RHO = 0.6`, `CALIBRATION_MIN_RECALL = 0.9`.
- Commit trailer for every commit (both lines):
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0175gLq9uam6M5Z9yNNxM4Ba
  ```

## Review Focus

1. `verify` on our own output equals the sidecar's `level`/`reasons`/`pages_unrepaired` (Task 3 test on `tests/fixtures` outputs made by the fake engine; Task 16 on the two real books).
2. Inferred page maps never report `coverage` (it is meaningless there) — Task 2/3 tests assert `coverage is None` and `anchored`/`decidable` present.
3. `page_dropped` is a page-level `warn`, never a document `low` (Task 3).
4. Exit code 3 for a scanned PDF with no markers, not a traceback (Task 4).
5. HTML report has no external URL in any `src`/`href` (Task 5 regex test).
6. Every committed corpus file has a licence line in `gt.json`/`MANIFEST.json`; `fetch.py --check` fails otherwise (Task 10).
7. Single-engine benchmark configs run with repair disabled, and the report says so (Task 12/13).
8. The calibration gate (Task 14) is enforced before Task 16 publishes anything.

---

## Index amendments (applied by Task 0 to `docs/superpowers/plans/2026-09-24-index.md`)

- **§1 layout**: `src/aidoc/markers.py`, `src/aidoc/verify.py`, `src/aidoc/verify_report.py`, `src/aidoc/server/api/verify.py`, `src/aidoc/mcp/tools_verify.py`; `bench/` (`corpus/make_synthetic.py`, `corpus/fetch.py`, `corpus/MANIFEST.json`, `corpus/<name>/gt.json`, `metrics.py`, `run.py`, `report.py`, `results/` gitignored except `*/summary.md` + `env.json` + `results.jsonl` of published runs copied into `docs/benchmarks/`), `docs/benchmarks/`, `tests/fixtures/{docling_recorded,markitdown_recorded}/`, `tests/fixtures/bench_expected_summary.md`.
- **§2 new rows**
  - A24 | `verify` purity. | `verify_markdown()` touches no store, writes no files, makes no network calls; CLI/API/MCP are shells around it.
  - A25 | Benchmark scope. | `bench/` is not in the wheel; GPU runs are manual on the dev host; CI runs only `--fake` plumbing and metric unit tests; results are published by copying into `docs/benchmarks/`.
  - A26 | Inferred page maps. | When markers are absent, `page_map.method = "inferred"`, `coverage = null`, `found = null`, and `anchored`/`decidable`/`confidence` are reported; `page_map_incomplete`/`page_map_misaligned`/`page_map_invalid` are not evaluated.
- **§3 models**: `VerifyReport` dataclass (spec §2.4 keys) with `to_json()`, `summary_text(lang)`, `to_html(thumbs: dict[int, bytes] | None)`; `EXIT_OK = 0, EXIT_FAILED = 1, EXIT_UNVERIFIABLE = 3, EXIT_USAGE = 4`.
- **§8 API**: `POST /verify` — multipart (`markdown`, optional `pdf`, fields `format`, `sample`, `pages`, `marker_regex`) | JSON `{doc_id}` | JSON `{markdown_path, pdf_path}` (loopback/token only) → `200 VerifyReport JSON`; errors `413 too_large`, `404 not_found`, `403 local_path_forbidden`, `422 invalid_input {detail}`, `409 unverifiable` **is not an error**: a report with `verifiable: false` is still `200`.
- **MCP spec §5.2**: `verify_document {doc_id}` (read, read_only, idempotent) → `VerifyOut`; `verify_markdown {doc_id | pdf_base64, markdown_base64, format?}` (convert) → `VerifyOut` (`summary`, `page_map`, `pages[:50]`, `pages_truncated`, `verifiable`, `hint`).
- **§7 config**: none (verify has no settings; `mcp.max_upload_mb` bounds `verify_markdown` payloads).

## File map

| Path | Change |
|---|---|
| `docs/superpowers/plans/2026-09-24-index.md`, `src/aidoc/models.py`, `tests/unit/test_models.py` | Task 0 |
| `src/aidoc/markers.py`, `tests/unit/test_markers.py` | Task 1 |
| `src/aidoc/pagemap.py` (`infer_pages`), `tests/unit/test_infer_pages.py` | Task 2 |
| `src/aidoc/verify.py`, `src/aidoc/probe.py` (per-page analysis export), `tests/unit/test_verify.py` | Task 3 |
| `src/aidoc/cli.py`, `src/aidoc/verify.py` (summary), `tests/unit/test_cli_verify.py` | Task 4 |
| `src/aidoc/verify_report.py`, `tests/unit/test_verify_report.py` | Task 5 |
| `src/aidoc/server/api/verify.py`, `src/aidoc/server/app.py`, `tests/api/test_verify.py` | Task 6 |
| `src/aidoc/mcp/tools_verify.py`, `src/aidoc/mcp/schemas.py`, `src/aidoc/mcp/server.py`, `tests/mcp/test_tool_verify.py` | Task 7 |
| `tests/integration/test_record_third_party.py`, `tests/fixtures/{docling_recorded,markitdown_recorded}/`, `tests/unit/test_verify_formats.py` | Task 8 |
| `bench/corpus/make_synthetic.py`, `bench/corpus/fonts.py`, `bench/corpus/texts/` (Gutenberg excerpts, header stripped), `bench/corpus/*/gt.json`, `tests/bench/test_make_synthetic.py`, `pyproject.toml` (`[dependency-groups].bench`) | Task 9 |
| `bench/corpus/fetch.py`, `bench/corpus/MANIFEST.json`, `tests/bench/test_fetch_manifest.py` | Task 10 |
| `bench/metrics.py`, `tests/bench/test_metrics.py` | Task 11 |
| `bench/run.py`, `bench/gpu_sampler.py`, `tests/bench/test_run_fake.py` | Task 12 |
| `bench/report.py`, `bench/NONCLAIMS.md`, `tests/fixtures/bench_expected_summary.md`, `tests/bench/test_report.py` | Task 13 |
| `bench/calibrate.py`, `tests/bench/test_calibrate.py` | Task 14 |
| `README.md`, `skill/SKILL.md`, `docs/benchmarks/<date>.md`, spec acceptance record, this file's deviations | Task 15 (docs) and Task 16 (GPU run + publish) |

---

### Task 0: Contracts — index amendments, `VerifyReport`, exit codes

**Files:**
- Modify: `docs/superpowers/plans/2026-09-24-index.md`, `src/aidoc/models.py`, `tests/unit/test_models.py`

**Interfaces:**
```python
@dataclass
class VerifyReport:
    verify_version: int
    aidoc_version: str
    generated_at: str                      # ISO 8601 with offset
    input: dict                            # {markdown, sha256, bytes, newline, format_detected, format_forced?, markers_found}
    pdf: dict | None                       # {path, sha256, pages, text_layer_pages, broken_fonts, broken_font_pages_count, blank_pages}
    verifiable: bool | str                 # True | False | "partial"
    page_map: dict | None
    summary: dict                          # {level, score, reasons, pages_flagged, pages_unrepaired, advisories}
    metrics: dict
    pages: list[dict]                      # entries with reasons and/or advisories
    previous: dict | None = None
    timing_s: dict = field(default_factory=dict)
    def to_json(self) -> dict: ...
    @property
    def level(self) -> str: ...            # summary["level"]
EXIT_OK, EXIT_FAILED, EXIT_UNVERIFIABLE, EXIT_USAGE = 0, 1, 3, 4
```

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_models.py (append)
def test_verify_report_shape():
    from aidoc.models import EXIT_FAILED, EXIT_OK, EXIT_UNVERIFIABLE, EXIT_USAGE, VerifyReport
    r = VerifyReport(verify_version=1, aidoc_version="0.1.0", generated_at="2026-10-05T10:00:00+08:00",
                     input={"markdown": "a.md", "sha256": "x", "bytes": 1, "newline": "lf", "format_detected": "aidoc", "markers_found": 3},
                     pdf=None, verifiable="partial", page_map={"expected": 3, "found": 3, "coverage": 1.0, "method": "aidoc"},
                     summary={"level": "ok", "score": 1.0, "reasons": [], "pages_flagged": 0, "pages_unrepaired": 0, "advisories": 0},
                     metrics={}, pages=[])
    j = r.to_json()
    assert j["verify_version"] == 1 and j["verifiable"] == "partial" and j["summary"]["level"] == "ok" and r.level == "ok"
    assert (EXIT_OK, EXIT_FAILED, EXIT_UNVERIFIABLE, EXIT_USAGE) == (0, 1, 3, 4)
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** + apply every index amendment verbatim. **Step 4: Run** `uv run pytest tests/unit/test_models.py -q` → pass.
- [ ] **Step 5: Commit** `feat(contracts): VerifyReport, verify exit codes, index amendments (spec 2026-10-05 verify)`.

---

### Task 1: `markers.py` — page-marker dialects

**Files:**
- Create: `src/aidoc/markers.py`, `tests/unit/test_markers.py`

**Interfaces:**
- `DIALECTS: dict[str, Dialect]` with `Dialect(name, pattern: re.Pattern | None, numbered: bool, note)` for `aidoc` (`<!-- page: (\d+) -->`), `pdfmux` (`<!--\s*page\s+(\d+)\s*-->`, numbered), `docling` (`<!-- page break -->`, unnumbered), `mineru` (`^---$` lines between sections, unnumbered — FULL-mode output only), `markitdown` (none for PDF; pptx `<!-- Slide number: (\d+) -->` recorded but ignored for verification).
- `detect(md: str) -> str`: first dialect whose pattern occurs ≥ 2 times, in the order aidoc → pdfmux → docling → mineru; else `"none"`.
- `to_aidoc(md: str, fmt: str, *, expected_pages: int | None, marker_regex: str | None = None) -> tuple[str, dict]`: returns Markdown rewritten with `<!-- page: N -->` markers plus info `{format, markers_found, numbered, applied: bool, reason}`. Numbered dialects: rename markers. Unnumbered: number sections 1..k in order **only if** `expected_pages is not None and k == expected_pages`, else `applied = False` (caller falls back to inference). Custom regex must have exactly one group (else `ValueError`). `fmt == "none"` → strips nothing, `applied = False`.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_markers.py
import pytest
from aidoc.markers import detect, to_aidoc


def test_detect_dialects():
    assert detect("<!-- page: 1 -->\na\n<!-- page: 2 -->\nb") == "aidoc"
    assert detect("<!-- page 1 -->\na\n<!-- page 2 -->\nb") == "pdfmux"
    assert detect("a\n<!-- page break -->\nb\n<!-- page break -->\nc") == "docling"
    assert detect("a\n\n---\n\nb\n\n---\n\nc") == "mineru"
    assert detect("plain text without any markers\n\n---\n\nonly one rule") == "none"


def test_numbered_dialect_is_renamed():
    md, info = to_aidoc("<!-- page 1 -->\na\n<!-- page 2 -->\nb", "pdfmux", expected_pages=2)
    assert md.count("<!-- page: ") == 2 and info["applied"] and info["markers_found"] == 2


def test_unnumbered_dialect_needs_matching_page_count():
    src = "a\n<!-- page break -->\nb\n<!-- page break -->\nc"
    md, info = to_aidoc(src, "docling", expected_pages=3)
    assert md.startswith("<!-- page: 1 -->") and "<!-- page: 3 -->" in md and info["applied"]
    md2, info2 = to_aidoc(src, "docling", expected_pages=5)          # Docling drops blank pages: 3 sections != 5 pages
    assert md2 == src and not info2["applied"] and info2["reason"] == "section_count_mismatch"


def test_custom_regex_requires_one_group():
    with pytest.raises(ValueError):
        to_aidoc("x", "custom", expected_pages=1, marker_regex=r"\[\[page\]\]")
    md, info = to_aidoc("[[p 1]]\na\n[[p 2]]\nb", "custom", expected_pages=2, marker_regex=r"\[\[p (\d+)\]\]")
    assert md.count("<!-- page: ") == 2
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → pass. **Step 5: Commit** `feat(verify): page-marker dialect detection and normalisation (aidoc, pdfmux, docling, mineru, custom)`.

---

### Task 2: `pagemap.infer_pages()` — page boundaries without markers

**Files:**
- Modify: `src/aidoc/pagemap.py`
- Create: `tests/unit/test_infer_pages.py`

**Interfaces:**
- `squash_with_map(s: str) -> tuple[str, list[int]]`: squashed text plus, for each squashed index, the original offset (so a position in the squashed string maps back).
- `infer_pages(md: str, layers: list[str], *, win=INFER_WIN, k=INFER_PROBES, min_chars=120) -> dict`: spec §2.2 algorithm → `{"method": "inferred", "expected": n, "found": None, "coverage": None, "anchored": int, "decidable": int, "confidence": float, "sections": {page: (start, end)}, "undecidable": [pages], "dropped_candidates": [pages]}`. `sections` are original-offset half-open ranges snapped to the nearest blank line; pages without an anchor between two anchored neighbours get an empty range at the boundary (they become `page_dropped` candidates in Task 3 when their layer is long).
- `apply_inferred(md: str, sections: dict[int, tuple[int, int]]) -> str`: inserts `<!-- page: N -->` at each section start (so the rest of `verify` can reuse `split_pages`/`flag_pages`). Pages with empty ranges still get a marker (empty section).

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_infer_pages.py
from pathlib import Path
from aidoc.pagemap import MARKER, apply_inferred, infer_pages, pdf_text_layers, split_pages

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _marked_markdown_from_pdf(pdf):
    # our own output stand-in: the PDF text layer itself, one section per page, with markers
    import pymupdf
    with pymupdf.open(pdf) as d:
        return "".join(f"<!-- page: {i} -->\n\n{p.get_text().strip()}\n\n" for i, p in enumerate(d, 1))


def test_infers_boundaries_of_a_marker_stripped_document():
    pdf = FIX / "paged_furniture.pdf"
    marked = _marked_markdown_from_pdf(pdf)
    stripped = MARKER.sub("", marked)
    res = infer_pages(stripped, pdf_text_layers(pdf))
    assert res["method"] == "inferred" and res["coverage"] is None and res["expected"] == 8
    assert res["anchored"] >= 6 and res["confidence"] >= 0.85          # page 4 is blank: undecidable by design
    rebuilt = apply_inferred(stripped, res["sections"])
    _, secs = split_pages(rebuilt)
    _, orig = split_pages(marked)
    for p in (1, 2, 3, 5, 6, 7, 8):
        assert secs[p].strip()[:40] == orig[p].strip()[:40], p        # boundaries land on the right paragraph


def test_monotone_filter_drops_out_of_order_anchor():
    pdf = FIX / "paged_furniture.pdf"
    layers = pdf_text_layers(pdf)
    marked = _marked_markdown_from_pdf(pdf)
    _, secs = split_pages(marked)
    swapped = "\n\n".join(secs[p] for p in (1, 2, 6, 4, 5, 3, 7, 8))   # pages 3 and 6 swapped, no markers
    res = infer_pages(swapped, layers)
    assert res["dropped_candidates"] and set(res["dropped_candidates"]) <= {3, 6}


def test_scanned_pdf_has_no_layers_so_nothing_is_anchored():
    res = infer_pages("some text that is not in any layer", pdf_text_layers(FIX / "scanned_cht.pdf"))
    assert res["anchored"] == 0 and res["confidence"] == 0.0 and res["sections"] == {}
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** reusing `_probes()` (same seed/window/uniqueness rules) and `_even_sample` is **not** used: every candidate page is probed (inference needs every page). Longest-increasing-subsequence on `(page, median_anchor_pos)`. Snap boundaries to the nearest `\n\n` within ±200 original characters, else to the nearest newline.
- [ ] **Step 4: Run** → pass; `uv run pytest tests/unit/test_pagemap.py -q` still green. **Step 5: Commit** `feat(pagemap): infer page boundaries from unique text-layer probes when a document has no markers`.

### Task 3: `verify.py` — the pure function

**Files:**
- Create: `src/aidoc/verify.py`, `tests/unit/test_verify.py`
- Modify: `src/aidoc/probe.py` (export `page_features(doc, index) -> {"ruled_grid": bool, "math": bool, "layer_chars": int}` built from the existing `_analyse_page`/`_ruled_grid` helpers; no behaviour change for `probe_file`)

**Interfaces:**
```python
def verify_markdown(md: str, pdf: Path | None, *, pages: int | None = None, fmt: str = "auto",
                    marker_regex: str | None = None, sample: int = 30, previous: dict | None = None,
                    input_name: str = "<markdown>") -> VerifyReport
```
Algorithm (spec §2.2–§2.4):
1. Normalise CRLF → LF (record `newline`); sha256 + bytes of the original.
2. `fmt == "auto"` → `markers.detect()`; `markers.to_aidoc(...)` with `expected_pages` from the PDF (or `pages`).
3. PDF given: `probe_file(pdf)` (error → `ValueError("pdf_unreadable: …")` → CLI exit 4); `layers = pdf_text_layers(pdf)`; `text_layer_pages = sum(len(l) >= 20 for l in layers)`.
4. Page map: markers applied → `pagemap.page_map()` + `invalid_markers()` + `spot_check(sample)` as `quality.assess` does; not applied and PDF with layers → `infer_pages()`, `apply_inferred()`, `page_map` per A26; not applied and (no PDF or no layers) → `verifiable = False` (no layers) or `"partial"` (no PDF but `pages` given → coverage only), `page_map = None` when nothing can be said.
5. Quality: call `quality.assess(md_marked, probe, pages, pdf=pdf, page_map_method=<fmt or "inferred">, layers=layers, last_resort=True)` **but** when inferred, pass a flag `skip_page_map_reasons=True` (new keyword, default False; it suppresses `page_map_incomplete/invalid/misaligned` and uses the inferred dict as `page_map`). Third-party outputs have no `repaired_by`, so every flagged page is unrepaired.
6. New per-page checks (PDF required): `page_dropped` (layer ≥ 120 squashed chars and section strip_markup non-space < 20, excluding `probe.blank_pages`) → page reason; advisories `table_missing_on_page` (`page_features().ruled_grid` and no table in the section), `formula_missing_on_page` (`math` and no `$` in the section), `images_missing` (count of `<!-- image missing -->`). Advisory-only entries do not count in `pages_flagged`/`pages_unrepaired`.
7. Level/score: from `assess` (+ `page_dropped` entries counted as unrepaired → `warn` unless a document reason already made it `low`).
8. Build `VerifyReport` (timings per phase).

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_verify.py
import json
from pathlib import Path
import pymupdf, pytest
from aidoc.pagemap import MARKER, split_pages
from aidoc.verify import verify_markdown

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _own_output(pdf):
    with pymupdf.open(pdf) as d:
        return "".join(f"<!-- page: {i} -->\n\n{p.get_text().strip()}\n\n" for i, p in enumerate(d, 1))


def test_clean_document_is_ok_and_json_matches_sidecar_keys():
    r = verify_markdown(_own_output(FIX / "paged_furniture.pdf"), FIX / "paged_furniture.pdf")
    j = r.to_json()
    assert r.level == "ok" and r.verifiable is True and j["page_map"]["coverage"] == 1.0 and j["input"]["format_detected"] == "aidoc"
    assert {"level", "score", "reasons", "pages_flagged", "pages_unrepaired"} <= set(j["summary"])
    from aidoc.reassess import doc_flags                       # Library can consume summary + page_map unchanged
    assert doc_flags({"quality": {**j["summary"], "page_map": j["page_map"], "page_check": 1}, "source_path": "x.pdf"}) == []


def test_dropped_pages_are_page_level_warn_not_low():
    md = _own_output(FIX / "paged_furniture.pdf")
    _, secs = split_pages(md)
    md = md.replace(secs[3], "\n\n").replace(secs[6], "\n\n")        # pages 3 and 6 silently emptied, markers kept
    r = verify_markdown(md, FIX / "paged_furniture.pdf")
    assert r.level == "warn" and [p["page"] for p in r.pages if "page_dropped" in p["reasons"]] == [3, 6]
    assert r.summary["pages_unrepaired"] == 2


def test_shifted_markers_are_low():
    md = MARKER.sub(lambda m: f"<!-- page: {int(m.group(1)) + 1} -->", _own_output(FIX / "paged_furniture.pdf"))
    r = verify_markdown(md, FIX / "paged_furniture.pdf")
    assert r.level == "low" and ("page_map_invalid" in r.summary["reasons"] or "page_map_misaligned" in r.summary["reasons"])


def test_no_markers_is_inferred_and_coverage_is_null():
    md = MARKER.sub("", _own_output(FIX / "paged_furniture.pdf"))
    r = verify_markdown(md, FIX / "paged_furniture.pdf")
    assert r.page_map["method"] == "inferred" and r.page_map["coverage"] is None and r.page_map["anchored"] >= 6
    assert "page_map_incomplete" not in r.summary["reasons"] and r.verifiable is True


def test_scanned_without_markers_is_unverifiable():
    r = verify_markdown("這是一些 OCR 出來的文字，沒有頁碼。", FIX / "scanned_cht.pdf")
    assert r.verifiable is False and r.page_map is None and r.level in ("ok", "warn", "low")


def test_no_pdf_partial_with_pages():
    md = _own_output(FIX / "text.pdf")
    r = verify_markdown(md, None, pages=3)
    assert r.verifiable == "partial" and r.page_map["coverage"] == 1.0 and r.page_map.get("alignment") is None
    with pytest.raises(ValueError):
        verify_markdown("x", None)                                     # neither pdf nor pages


def test_broken_text_layer_page_is_flagged_without_repair_info():
    # the committed broken_tounicode.pdf: page 1 glyph-id text; use the raw text layer as "third-party output"
    md = _own_output(FIX / "broken_tounicode.pdf")
    r = verify_markdown(md, FIX / "broken_tounicode.pdf")
    flagged = {p["page"]: p["reasons"] for p in r.pages if p.get("reasons")}
    assert 1 in flagged and "broken_text_layer" in flagged[1] and 2 not in flagged
    assert r.summary["pages_unrepaired"] >= 1


def test_table_advisory_on_text_pdf_page_2_when_table_missing():
    md = _own_output(FIX / "text.pdf")                                # plain text layer: table cells but no GFM table
    r = verify_markdown(md, FIX / "text.pdf")
    adv = {p["page"]: p.get("advisories", []) for p in r.pages}
    assert "table_missing_on_page" in adv.get(2, []) and r.level in ("ok", "warn")   # advisory never lowers the level


def test_crlf_input_and_custom_regex():
    md = _own_output(FIX / "text.pdf").replace("<!-- page: ", "[[p ").replace(" -->", "]]").replace("\n", "\r\n")
    r = verify_markdown(md, FIX / "text.pdf", fmt="custom", marker_regex=r"\[\[p (\d+)\]\]")
    assert r.input["newline"] == "crlf" and r.page_map["coverage"] == 1.0 and r.input["format_forced"] is True
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (`quality.assess(..., skip_page_map_reasons=False)` is the only change to `quality.py`; keep its default behaviour byte-identical). `probe.page_features` reuses `_analyse_page` output keys.
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_verify.py tests/unit/test_quality.py -q` → pass. **Step 5: Commit** `feat(verify): verify_markdown — page map (markers or inferred), per-page checks, page_dropped, advisories`.

---

### Task 4: CLI `aidoc verify` + human summary + exit codes

**Files:**
- Modify: `src/aidoc/cli.py`, `src/aidoc/verify.py` (`summary_text(report, lang)`, `resolve_inputs(path, pdf) -> (md_text, pdf_path, previous, pages)` for the output-directory form using `output.read_sidecar` and `reassess._source_pdf`-like lookup without the store), `tests/unit/test_cli_verify.py`

**Interfaces:** spec §2.1 options; `cmd_verify(args) -> int`. Directory form: `aidoc verify out/<stem>/` → `<stem>.md` + sidecar; PDF from `--pdf`, else sidecar `source` if it exists and sha matches, else `data/work/*/src.pdf` lookup via `Store` **read-only** (allowed in the CLI shell, not in `verify_markdown`). `--json -` writes JSON to stdout and the summary to stderr.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_cli_verify.py
import json, subprocess, sys
from pathlib import Path
import pymupdf
from aidoc.cli import main
from aidoc.pagemap import MARKER

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _own(pdf):
    with pymupdf.open(pdf) as d:
        return "".join(f"<!-- page: {i} -->\n\n{p.get_text().strip()}\n\n" for i, p in enumerate(d, 1))


def test_exit_codes(tmp_path, capsys):
    ok = tmp_path / "ok.md"; ok.write_text(_own(FIX / "paged_furniture.pdf"), encoding="utf-8")
    assert main(["verify", str(ok), "--pdf", str(FIX / "paged_furniture.pdf")]) == 0
    out = capsys.readouterr().out
    assert "level: ok" in out and len(out.strip().splitlines()) <= 12
    bad = tmp_path / "bad.md"
    bad.write_text(MARKER.sub(lambda m: f"<!-- page: {int(m.group(1)) + 1} -->", ok.read_text(encoding="utf-8")), encoding="utf-8")
    assert main(["verify", str(bad), "--pdf", str(FIX / "paged_furniture.pdf")]) == 1
    scan = tmp_path / "scan.md"; scan.write_text("沒有頁碼的 OCR 文字", encoding="utf-8")
    assert main(["verify", str(scan), "--pdf", str(FIX / "scanned_cht.pdf")]) == 3
    assert main(["verify", str(scan), "--pdf", str(FIX / "scanned_cht.pdf"), "--allow-unverifiable"]) == 0
    assert main(["verify", str(tmp_path / "missing.md"), "--pdf", str(FIX / "text.pdf")]) == 4
    assert main(["verify", str(ok), "--pdf", str(FIX / "corrupt.pdf")]) == 4


def test_fail_on_warn_and_json_output(tmp_path, capsys):
    md = _own(FIX / "paged_furniture.pdf")
    from aidoc.pagemap import split_pages
    _, secs = split_pages(md)
    p = tmp_path / "w.md"; p.write_text(md.replace(secs[3], "\n\n"), encoding="utf-8")
    assert main(["verify", str(p), "--pdf", str(FIX / "paged_furniture.pdf")]) == 0            # warn passes by default
    assert main(["verify", str(p), "--pdf", str(FIX / "paged_furniture.pdf"), "--fail-on", "warn"]) == 1
    assert main(["verify", str(p), "--pdf", str(FIX / "paged_furniture.pdf"), "--json", str(tmp_path / "r.json")]) == 0
    j = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert j["summary"]["level"] == "warn" and j["pages"][0]["reasons"] == ["page_dropped"]
    capsys.readouterr()
    assert main(["verify", str(p), "--pdf", str(FIX / "paged_furniture.pdf"), "--json", "-"]) == 0
    assert json.loads(capsys.readouterr().out)["verify_version"] == 1


def test_output_directory_form_uses_sidecar(tmp_path, monkeypatch):
    out = tmp_path / "out" / "text"; out.mkdir(parents=True)
    (out / "text.md").write_text(_own(FIX / "text.pdf"), encoding="utf-8")
    (out / "text.json").write_text(json.dumps({"source": str(FIX / "text.pdf"), "sha256": __import__("aidoc.names", fromlist=["file_sha256"]).file_sha256(FIX / "text.pdf"),
                                              "pages": 3, "engine": "docling", "quality": {"level": "ok", "score": 1.0}}), encoding="utf-8")
    assert main(["verify", str(out), "--json", str(tmp_path / "r.json")]) == 0
    j = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert j["pdf"]["pages"] == 3 and j["previous"]["engine"] == "docling"


def test_subprocess_entry_point(tmp_path):
    p = tmp_path / "a.md"; p.write_text(_own(FIX / "text.pdf"), encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "aidoc.cli", "verify", str(p), "--pdf", str(FIX / "text.pdf")], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0 and "level: ok" in r.stdout
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (argparse subcommand per spec §2.1; `--lang` affects summary wording only). **Step 4: Run** → pass. **Step 5: Commit** `feat(cli): aidoc verify — summary, --json/--html, --fail-on, exit codes 0/1/3/4`.

---

### Task 5: HTML report

**Files:**
- Create: `src/aidoc/verify_report.py`, `tests/unit/test_verify_report.py`
- Modify: `src/aidoc/verify.py` (`VerifyReport.to_html(thumbs)`), `src/aidoc/cli.py` (`--html`, `--thumbnails`)

**Interfaces:** `render_html(report: VerifyReport, thumbs: dict[int, bytes] | None, lang: str) -> str` — single document, inline CSS with light/dark variables (`prefers-color-scheme`), header, reasons/advisories counts, page table (page, reasons, advisories, key metrics, thumbnail `<img src="data:image/png;base64,…">` when given), inferred-map confidence, version/time footer. `thumbnails(pdf, pages, width=160, max_pages=200) -> dict[int, bytes]` in `verify_report.py` (PyMuPDF). No `<script>`.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_verify_report.py
import re
from pathlib import Path
import pymupdf
from aidoc.verify import verify_markdown
from aidoc.verify_report import render_html, thumbnails
from aidoc.pagemap import split_pages

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _own(pdf):
    with pymupdf.open(pdf) as d:
        return "".join(f"<!-- page: {i} -->\n\n{p.get_text().strip()}\n\n" for i, p in enumerate(d, 1))


def test_html_is_self_contained_and_lists_flagged_pages():
    md = _own(FIX / "paged_furniture.pdf"); _, secs = split_pages(md)
    r = verify_markdown(md.replace(secs[3], "\n\n"), FIX / "paged_furniture.pdf")
    html = render_html(r, thumbnails(FIX / "paged_furniture.pdf", [3]), "cht")
    assert "<script" not in html.lower()
    for m in re.finditer(r'(?:src|href)="([^"]+)"', html):
        assert m.group(1).startswith("data:") or m.group(1).startswith("#"), m.group(1)
    assert "page_dropped" in html and 'data:image/png;base64,' in html and "prefers-color-scheme" in html
    assert re.search(r"<tr[^>]*>\s*<td[^>]*>\s*3\s*</td>", html)


def test_thumbnails_cap():
    t = thumbnails(FIX / "big.pdf", list(range(1, 46)), width=80, max_pages=10)
    assert len(t) == 10 and all(v[:8] == b"\x89PNG\r\n\x1a\n" for v in t.values())
```

- [ ] **Step 2–4:** fail → implement → pass (`uv run pytest tests/unit/test_verify_report.py tests/unit/test_cli_verify.py -q`). **Step 5: Commit** `feat(verify): self-contained HTML report with optional page thumbnails`.

---

### Task 6: `POST /api/verify`

**Files:**
- Create: `src/aidoc/server/api/verify.py`, `tests/api/test_verify.py`
- Modify: `src/aidoc/server/app.py` (include router), `src/aidoc/server/api/documents.py` (helper `source_pdf_for(doc)` shared with reassess)

**Interfaces:** three body shapes (spec §2.6); multipart size bound = `limits.upload_max_bytes` (413); `doc_id` form reads the document's Markdown + source PDF (original or work copy; neither → `verify` without PDF using `pages` from the row → `partial`); local-path form guarded exactly like `POST /jobs` local paths (`auth.allow_local_paths(request)`); runs in `run_in_threadpool`; response = `VerifyReport.to_json()` + `workspace` envelope.

- [ ] **Step 1: Failing tests**

```python
# tests/api/test_verify.py
import io, json
from pathlib import Path
import pymupdf

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _own(pdf):
    with pymupdf.open(pdf) as d:
        return "".join(f"<!-- page: {i} -->\n\n{p.get_text().strip()}\n\n" for i, p in enumerate(d, 1))


def test_multipart_verify(client):
    files = {"markdown": ("a.md", io.BytesIO(_own(FIX / "text.pdf").encode()), "text/markdown"),
             "pdf": ("text.pdf", (FIX / "text.pdf").read_bytes(), "application/pdf")}
    r = client.post("/api/verify", files=files, data={"format": "auto", "sample": "0"})
    assert r.status_code == 200 and r.json()["summary"]["level"] == "ok" and r.json()["workspace"] == "default"


def test_doc_id_form_uses_library_document(client_with_document):
    c, doc_id = client_with_document("text.pdf")          # converted with the fake engine by the fixture
    r = c.post("/api/verify", json={"doc_id": doc_id})
    body = r.json()
    assert r.status_code == 200 and body["previous"]["engine"] and body["summary"]["level"] in ("ok", "warn")
    assert c.post("/api/verify", json={"doc_id": "nope"}).status_code == 404


def test_local_path_form_is_guarded(client, remote_client):
    body = {"markdown_path": str(FIX / "text.pdf"), "pdf_path": str(FIX / "text.pdf")}
    assert remote_client.post("/api/verify", json=body).status_code == 403     # non-loopback, no token
    assert client.post("/api/verify", json={"markdown_path": str(FIX / "missing.md"), "pdf_path": str(FIX / "text.pdf")}).status_code == 422


def test_too_large(client, monkeypatch):
    client.app.state.ctx.config.limits.upload_max_bytes = 10
    files = {"markdown": ("a.md", io.BytesIO(b"x" * 100), "text/markdown")}
    assert client.post("/api/verify", files=files, data={"pages": "1"}).status_code == 413
```

- [ ] **Step 2–4:** fail → implement → `uv run pytest tests/api -q` green (add `client_with_document`/`remote_client` fixtures to `tests/api/conftest.py` if absent, following how the documents tests create a converted document with `AIDOC_FAKE_ENGINES=1`). **Step 5: Commit** `feat(api): POST /api/verify (multipart, doc_id, guarded local paths)`.

---

### Task 7: MCP tools `verify_document` and `verify_markdown`

**Files:**
- Create: `src/aidoc/mcp/tools_verify.py`, `tests/mcp/test_tool_verify.py`
- Modify: `src/aidoc/mcp/schemas.py` (`VerifyOut`, `PageMapBrief` reuse), `src/aidoc/mcp/server.py` (register), `src/aidoc/mcp/docs.py` (if a helper for the source PDF is shared)

**Interfaces:** `VerifyOut(doc_id: str | None, verifiable: bool | str, summary: QualityFull + pages_flagged + pages_unrepaired + advisories, page_map: PageMapBrief | None, inferred: bool, pages: list[PageEntry] (≤ 50), pages_truncated: bool, hint: str | None)`; `verify_document(doc_id)` scope read; `verify_markdown(markdown_base64, doc_id=None, pdf_base64=None, format="auto")` scope convert, exactly one of `doc_id`/`pdf_base64`, payload ≤ `mcp.max_upload_mb`; both fit `response_token_budget` via the existing `_fit_*` pattern (drop `pages` tail first).

- [ ] **Step 1: Failing tests** (follow `tests/mcp/test_tool_read.py` fixtures)

```python
# tests/mcp/test_tool_verify.py
import base64
async def test_verify_document_requires_read_scope_and_returns_summary(mcp_client_with_doc, mcp_client_noscope):
    c, doc_id = mcp_client_with_doc
    out = await c.call_tool("verify_document", {"doc_id": doc_id})
    sc = out.structuredContent
    assert not out.isError and sc["summary"]["level"] in ("ok", "warn") and sc["page_map"]["coverage"] == 1.0 and sc["inferred"] is False
    bad = await mcp_client_noscope.call_tool("verify_document", {"doc_id": doc_id})
    assert bad.isError and bad.structuredContent["error"]["code"] == "forbidden_scope"

async def test_verify_markdown_against_library_pdf(mcp_client_with_doc):
    c, doc_id = mcp_client_with_doc
    md = base64.b64encode("第一頁\n\n---\n\n第二頁\n\n---\n\n第三頁".encode()).decode()
    out = await c.call_tool("verify_markdown", {"doc_id": doc_id, "markdown_base64": md, "format": "mineru"})
    assert not out.isError and out.structuredContent["verifiable"] in (True, False, "partial")
    both = await c.call_tool("verify_markdown", {"doc_id": doc_id, "pdf_base64": "AAAA", "markdown_base64": md})
    assert both.isError and both.structuredContent["error"]["code"] == "invalid_arguments"

async def test_budget_truncates_pages(mcp_client_with_big_warn_doc):
    c, doc_id = mcp_client_with_big_warn_doc                 # 45-page fake doc with many flagged pages
    out = await c.call_tool("verify_document", {"doc_id": doc_id})
    sc = out.structuredContent
    assert len(sc["pages"]) <= 50 and (sc["pages_truncated"] or len(sc["pages"]) < 50)
```

- [ ] **Step 2–4:** fail → implement → `uv run pytest tests/mcp -q` green. **Step 5: Commit** `feat(mcp): verify_document and verify_markdown tools`.

---

### Task 8: Third-party native outputs — record fixtures, test the dialects end to end

**Files:**
- Create: `tests/integration/test_record_third_party.py` (`@slow`; writes `tests/fixtures/docling_recorded/furniture8/export.md` via `export_to_markdown(page_break_placeholder="<!-- page break -->")` and `tests/fixtures/markitdown_recorded/furniture8/convert.md` via `MarkItDown().convert()`, both from `paged_furniture.pdf`, through the engine envs' Python with a small script — no new runner), `tests/unit/test_verify_formats.py`
- Modify: `tests/fixtures/README.md` (rows for the recorded files)

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_verify_formats.py
from pathlib import Path
import pytest
from aidoc.verify import verify_markdown

FIX = Path(__file__).resolve().parents[1] / "fixtures"
PDF = FIX / "paged_furniture.pdf"


def test_mineru_native_markdown_is_inferred():
    md = (FIX / "mineru_recorded" / "furniture8" / "markdown.md").read_text(encoding="utf-8")   # real MinerU 4.0.7 output
    r = verify_markdown(md, PDF, fmt="auto")
    assert r.input["format_detected"] in ("none", "mineru") and r.page_map["method"] == "inferred"
    assert r.page_map["anchored"] >= 6 and r.level != "low"


@pytest.mark.skipif(not (FIX / "docling_recorded" / "furniture8" / "export.md").exists(), reason="record with the slow test first")
def test_docling_native_placeholder_output():
    md = (FIX / "docling_recorded" / "furniture8" / "export.md").read_text(encoding="utf-8")
    r = verify_markdown(md, PDF, fmt="docling")
    # Docling drops the blank page 4 -> 7 sections for 8 pages -> markers not applied -> inferred
    assert r.page_map["method"] in ("inferred", "docling") and r.level != "low"


@pytest.mark.skipif(not (FIX / "markitdown_recorded" / "furniture8" / "convert.md").exists(), reason="record first")
def test_markitdown_native_output_inferred():
    md = (FIX / "markitdown_recorded" / "furniture8" / "convert.md").read_text(encoding="utf-8")
    r = verify_markdown(md, PDF)
    assert r.page_map["method"] == "inferred" and r.page_map["anchored"] >= 5
```

- [ ] **Step 2:** run the slow recorder on the GPU host (`uv run pytest -m slow tests/integration/test_record_third_party.py -q`), commit the two small `.md` files; run the unit tests → pass (the `skipif`s are only for machines that have not recorded — in acceptance mode `AIDOC_REQUIRE_MANUAL=1` the recorder's absence must **fail**: route the existence check through `tests/manual.py` with a new `recorded_fixture(name)` helper that fails when required).
- [ ] **Step 3: Commit** `test(verify): third-party native outputs (MinerU, Docling, MarkItDown) verified end to end`.

### Task 9: Benchmark corpus Tier A — `make_synthetic.py`

**Files:**
- Create: `bench/__init__.py` (empty; keeps tests importable), `bench/corpus/make_synthetic.py`, `bench/corpus/fonts.py`, `bench/corpus/texts/README.md` + `bench/corpus/texts/*.txt` (Gutenberg zh-Hant/en excerpts, PG header/footer stripped, with a `SOURCES.json` naming book ids and URLs), `bench/corpus/<name>/gt.json` (committed), `tests/bench/__init__.py`, `tests/bench/test_make_synthetic.py`
- Modify: `pyproject.toml` (`[dependency-groups] bench = ["rapidfuzz>=3", "augraphy>=8", "pillow>=10", "img2pdf>=0.5", "matplotlib>=3.8"]`), `.gitignore` (`bench/corpus/**/*.pdf` except `bench/corpus/small/`; `bench/results/`)

**Interfaces:**
- `fonts.ensure_noto(cache=bench/corpus/.fonts) -> dict[str, Path]`: downloads Noto Sans CJK TC + Noto Serif CJK TC (pinned URLs + sha256 from the Google Fonts/GitHub `notofonts` release; OFL 1.1) — never falls back to system fonts; `--offline` fails loudly.
- `make_synthetic.py [--small] [--only NAME] [--out bench/corpus]`: builds every Tier A item of spec §3.2 (`--small`: one item per category, 3 pages each, into `bench/corpus/small/`, **committed**, ≤ 2 MB total). Deterministic: seeds derived from the item name; Augraphy pipeline seeded; PDF metadata dates fixed. Writes `gt.json` (spec §3.3) next to each PDF; `page_text` is what was typeset (exact strings), `tables` cell matrices, `formulas` LaTeX, `defects` for broken/dropped variants. Broken-ToUnicode variants reuse `tests/fixtures/make_fixtures.make_broken_tounicode_pdf()`'s technique generalised (`break_tounicode(doc, page_indices, font)`). `syn-dropped-pages` writes **Markdown** files (`dropped.md`, `shifted.md`, `swapped.md`) derived from `syn-text-zh-01`'s GT plus the defect list.
- `gt.json` includes `generator.version`; changing generation logic bumps it and regenerates committed GT.

- [ ] **Step 1: Failing tests**

```python
# tests/bench/test_make_synthetic.py
import hashlib, json, subprocess, sys
from pathlib import Path
import pymupdf, pytest

BENCH = Path(__file__).resolve().parents[2] / "bench"
pytestmark = pytest.mark.skipif(not (BENCH / "corpus" / ".fonts").exists() and not __import__("os").environ.get("AIDOC_BENCH_FETCH"),
                                reason="Noto fonts not cached; set AIDOC_BENCH_FETCH=1 to download")


def test_small_corpus_is_deterministic_and_matches_committed_gt(tmp_path):
    subprocess.run([sys.executable, str(BENCH / "corpus" / "make_synthetic.py"), "--small", "--out", str(tmp_path)], check=True)
    for gt_path in sorted((BENCH / "corpus" / "small").glob("*/gt.json")):
        committed = json.loads(gt_path.read_text(encoding="utf-8"))
        fresh = json.loads((tmp_path / "small" / gt_path.parent.name / "gt.json").read_text(encoding="utf-8"))
        assert fresh == committed, gt_path.parent.name
        pdf = tmp_path / "small" / gt_path.parent.name / (gt_path.parent.name + ".pdf")
        if pdf.exists():
            with pymupdf.open(pdf) as d:
                assert d.page_count == committed["pages"]
                if committed["kind"] == "scan":
                    assert all(len(p.get_text().strip()) == 0 for p in d)          # image-only
                if committed["kind"] == "text":
                    assert "Noto" in " ".join(f[3] for f in d[0].get_fonts())      # OFL font embedded, never msjh


def test_broken_variant_has_glyph_id_layer_on_defect_pages(tmp_path):
    subprocess.run([sys.executable, str(BENCH / "corpus" / "make_synthetic.py"), "--small", "--only", "syn-broken-tounicode-a", "--out", str(tmp_path)], check=True)
    gt = json.loads((tmp_path / "small" / "syn-broken-tounicode-a" / "gt.json").read_text(encoding="utf-8"))
    from aidoc.probe import probe_file
    pr = probe_file(tmp_path / "small" / "syn-broken-tounicode-a" / "syn-broken-tounicode-a.pdf")
    assert set(pr.broken_font_pages) == set(gt["defects"]["broken_text_layer_pages"]) and pr.broken_fonts


def test_every_gt_has_a_license_line():
    for gt_path in (BENCH / "corpus").rglob("gt.json"):
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        assert gt["license"] and ("Public domain" in gt["license"] or "OFL" in gt["license"]), gt_path
```

- [ ] **Step 2: Run** `AIDOC_BENCH_FETCH=1 uv run --group bench pytest tests/bench/test_make_synthetic.py -q` → FAIL. **Step 3: Implement**; generate and commit `bench/corpus/small/**` (PDFs + GT) and the full-size `gt.json` files (PDFs regenerated on demand). **Step 4:** tests pass; `uv run --group bench python bench/corpus/make_synthetic.py` full run ≤ 10 min on the dev host (record time in deviations).
- [ ] **Step 5: Commit** `bench: synthetic Tier A corpus generator (zh-Hant text/scan, broken ToUnicode, tables, formulas, 1200-page book) with ground truth`.

---

### Task 10: Tier B fetcher and licence manifest

**Files:**
- Create: `bench/corpus/fetch.py`, `bench/corpus/MANIFEST.json`, `tests/bench/test_fetch_manifest.py`

**Interfaces:**
- `MANIFEST.json`: list of `{"name", "tier": "B", "url", "license", "license_url", "fetched_on", "sha256", "pages", "kind", "lang", "purpose", "notes", "drop_pages": []}`; the `license` value must be one of `OGDL-1.0`, `CC-BY-4.0`, `CC-BY-SA-4.0`, `CC0-1.0`, `US-PD`, `PD` (public domain scan), `research-only-no-redistribution` (only for OmniDocBench/olmOCR-Bench sanity entries, which `fetch.py` downloads into `bench/corpus/external/` and the report never includes in the main tables).
- `fetch.py [--check] [--only NAME] [--out bench/corpus/b]`: `--check` validates the manifest (required keys, licence enum, URL scheme, 64-hex sha); without it downloads missing files, verifies sha256, applies `drop_pages` (PII removal with PyMuPDF, recorded), writes `gt.json` with `gt_level: "L3"` (pages, kind, lang) and the licence text. Missing/404 → the entry is marked `missing` in `fetch.status.json`; never aborts the whole run.
- The initial manifest lists the candidates from spec §3.2 Tier B with `sha256` filled in by the implementer after a manual licence check of each landing page (write the check date into `fetched_on`).

- [ ] **Step 1: Failing tests**

```python
# tests/bench/test_fetch_manifest.py
import json, subprocess, sys
from pathlib import Path
import pytest
from bench.corpus import fetch as F

BENCH = Path(__file__).resolve().parents[2] / "bench"


def test_manifest_validates():
    problems = F.check_manifest(json.loads((BENCH / "corpus" / "MANIFEST.json").read_text(encoding="utf-8")))
    assert problems == []


@pytest.mark.parametrize("bad,msg", [
    ({"license": "CC-BY-NC-4.0"}, "license"),
    ({"license_url": ""}, "license_url"),
    ({"sha256": "abc"}, "sha256"),
    ({"url": "ftp://x"}, "url"),
])
def test_manifest_rejects_bad_entries(bad, msg):
    entry = {"name": "x", "tier": "B", "url": "https://example.org/a.pdf", "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
             "fetched_on": "2026-10-05", "sha256": "0" * 64, "pages": 3, "kind": "text", "lang": "cht", "purpose": "t", "notes": "", "drop_pages": []}
    assert any(msg in p for p in F.check_manifest([{**entry, **bad}]))


def test_fetch_marks_missing_and_verifies_sha(tmp_path):
    entry = {"name": "x", "tier": "B", "url": "https://example.org/a.pdf", "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
             "fetched_on": "2026-10-05", "sha256": "0" * 64, "pages": 1, "kind": "text", "lang": "en", "purpose": "t", "notes": "", "drop_pages": []}
    def opener(url, timeout=None):
        raise OSError("404")
    status = F.fetch_all([entry], tmp_path, opener=opener, log=lambda s: None)
    assert status["x"] == "missing"
    import io
    good = (Path(__file__).resolve().parents[1] / "fixtures" / "text.pdf").read_bytes()
    status = F.fetch_all([{**entry, "sha256": __import__("hashlib").sha256(good).hexdigest(), "pages": 3}], tmp_path,
                         opener=lambda u, timeout=None: io.BytesIO(good), log=lambda s: None)
    assert status["x"] == "ok" and json.loads((tmp_path / "x" / "gt.json").read_text(encoding="utf-8"))["gt_level"] == "L3"
```

- [ ] **Step 2–4:** fail → implement → pass. The initial `MANIFEST.json` must pass `--check` with real entries (at least: 2 行政院公報 PDFs, 2 立法院公報 PDFs, 3 arXiv CC BY PDFs, 2 NIST PDFs; others may be added in Task 16). **Step 5: Commit** `bench: Tier B fetcher with licence manifest (OGDL, CC BY, US-PD; research-only sources kept out of the tables)`.

---

### Task 11: Metrics

**Files:**
- Create: `bench/metrics.py`, `tests/bench/test_metrics.py`

**Interfaces:**
- `ned(gt: str, pred: str) -> float` (1 − normalized Levenshtein on `squash`ed strings; both empty → 1.0; gt non-empty & pred empty → 0.0); `cer(gt, pred) -> float`.
- `page_scores(gt: dict, md: str, page_map_sections: dict[int, str]) -> dict[int, dict]` → per page `{ned, cer, dropped: bool}`; pages absent from `page_map_sections` → pred `""`.
- `table_teds(gt_cells: list[list[str]], pred_markdown_section: str, structure_only=False) -> float`: converts the first GFM/HTML table of the section to an HTML `<table>` and the GT cells to HTML, then TEDS via `table_recognition_metric` if importable **and** its licence is confirmed in `bench/THIRD_PARTY.md`; otherwise the in-repo `apted_teds()` (tree edit distance on the HTML DOM, PubTabNet's definition). Both paths covered by the same tests.
- `formula_ned(gt_latex: list[str], section: str) -> float`: extract `$…$`/`$$…$$`, normalise (strip spaces, `\left`/`\right`, `\,`), best-match each GT formula (Hungarian not needed: greedy by NED), average.
- `recovery_rate(defect_pages, page_scores, threshold=0.90) -> float`; `dropped_rate(gt, page_scores) -> float`.
- `spearman(xs, ys) -> float`, `precision_recall(pred_set, gt_set) -> tuple[float, float]`.

- [ ] **Step 1: Failing tests**

```python
# tests/bench/test_metrics.py
import math, pytest
from bench import metrics as M


def test_ned_and_cer_basics():
    assert M.ned("abc", "abc") == 1.0 and M.ned("", "") == 1.0 and M.ned("abc", "") == 0.0
    assert math.isclose(M.ned("kitten", "sitting"), 1 - 3 / 7, rel_tol=1e-6)
    assert math.isclose(M.cer("臺灣大學", "台灣大學"), 0.25)                      # NFKC does not fold 臺/台 → 1 substitution
    assert M.ned("The quick-\nbrown", "the quick brown") == 1.0              # squash: case, hyphenation, spaces


def test_table_teds_perfect_and_structure_only():
    gt = [["A", "B"], ["1", "2"]]
    md = "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    assert M.table_teds(gt, md) == pytest.approx(1.0)
    assert M.table_teds(gt, "| A | B |\n| --- | --- |\n| 1 | 9 |\n", structure_only=True) == pytest.approx(1.0)
    assert M.table_teds(gt, "| A | B |\n| --- | --- |\n| 1 | 9 |\n") < 1.0
    assert M.table_teds(gt, "no table here") == 0.0
    assert M.table_teds(gt, "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>") == pytest.approx(1.0)


def test_formula_ned_normalises():
    assert M.formula_ned([r"\sum_{i=1}^{n} x_i"], r"Text $\sum_{i=1}^{n}x_i$ more") == pytest.approx(1.0)
    assert M.formula_ned([r"\frac{a}{b}"], r"$$\left(\frac{a}{b}\right)$$") == pytest.approx(1.0)
    assert M.formula_ned([r"\frac{a}{b}"], "no math") == 0.0


def test_rates_and_rank_stats():
    scores = {1: {"ned": 0.95, "dropped": False}, 2: {"ned": 0.5, "dropped": False}, 3: {"ned": 0.0, "dropped": True}}
    assert M.recovery_rate([1, 2], scores) == 0.5
    assert M.dropped_rate({"pages": 3, "blank_pages": []}, scores) == pytest.approx(1 / 3)
    assert M.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert M.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert M.precision_recall({1, 2, 3}, {2, 3, 4}) == (pytest.approx(2 / 3), pytest.approx(2 / 3))
```

- [ ] **Step 2–4:** fail → implement → `uv run --group bench pytest tests/bench/test_metrics.py -q` pass. **Step 5: Commit** `bench: metrics — NED/CER, TEDS (library or in-repo APTED), formula NED, recovery/dropped rates, rank stats`.

---

### Task 12: Runner

**Files:**
- Create: `bench/run.py`, `bench/gpu_sampler.py`, `tests/bench/test_run_fake.py`

**Interfaces:**
- `run.py --corpus A|A-small|B|C[,…] --configs auto,auto-norepair,docling,mineru,markitdown,vlm,raw-mineru,… --out DIR [--repeat N] [--fake] [--resume]`.
- Per (config, sample): `AIDOC_DATA=<out>/data-<config>`, `AIDOC_MODELS` = the user's models dir (resolved from the current config so nothing is re-downloaded), output root `<out>/out-<config>`; command `uv run aidoc convert <pdf> -o <root> --force [--engine X] --json`; env `AIDOC_BENCH_NO_REPAIR=1` for single-engine configs and `auto-norepair` (pipeline honours it: `check_document` skips `repair_pages` and logs `repair disabled (benchmark)`; **this env is read only in `pipeline.check_document` and documented there**); `vlm` config present only if `paths.ready_marker("vlm").exists()`.
- Warm-up: one 3-page sample per engine before timing. Timing: wall clock of the subprocess; `gpu_sampler.Sampler()` thread calls `nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits` every 0.5 s (absent → `null`).
- After each conversion: read the sidecar + Markdown, compute `metrics.page_scores` using the sidecar page map (our configs) or `verify`'s inferred map (`raw-*` configs), table/formula metrics where GT has them, and run `aidoc.verify.verify_markdown()` on the output (its `score`/`level` are stored for Task 14). Append one JSON line to `results.jsonl`: `{config, sample, tier, gt_level, status, engine_used, tried, seconds, seconds_excl_startup, peak_vram_mb, pages, metrics: {...}, verify: {score, level, pages_flagged}, sidecar_path}`.
- `--resume` skips (config, sample) pairs already in `results.jsonl`. `env.json` written at start (spec §3.5 fields; `uv.lock` sha256 of each env; engine versions from `.ready` files).
- `--fake`: sets `AIDOC_FAKE_ENGINES=1` and limits to `A-small`; used by CI.

- [ ] **Step 1: Failing test**

```python
# tests/bench/test_run_fake.py
import json, subprocess, sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2] / "bench"


def test_fake_run_produces_results_env_and_summary(tmp_path):
    out = tmp_path / "run"
    r = subprocess.run([sys.executable, str(BENCH / "run.py"), "--corpus", "A-small", "--configs", "auto,docling", "--out", str(out), "--fake"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {row["config"] for row in rows} == {"auto", "docling"} and all("ned" in row["metrics"] for row in rows if row["gt_level"] == "L1")
    assert all(row["status"] in ("done", "low", "warn", "failed") for row in rows)
    env = json.loads((out / "env.json").read_text(encoding="utf-8"))
    assert "aidoc_commit" in env and "engines" in env and env["fake"] is True
    assert (out / "summary.md").exists()
    # resume: a second run adds nothing
    r2 = subprocess.run([sys.executable, str(BENCH / "run.py"), "--corpus", "A-small", "--configs", "auto,docling", "--out", str(out), "--fake", "--resume"],
                        capture_output=True, text=True, encoding="utf-8")
    assert r2.returncode == 0 and len((out / "results.jsonl").read_text(encoding="utf-8").splitlines()) == len(rows)


def test_single_engine_configs_disable_repair(tmp_path, monkeypatch):
    from bench.run import env_for_config
    assert env_for_config("docling")["AIDOC_BENCH_NO_REPAIR"] == "1" and env_for_config("auto-norepair")["AIDOC_BENCH_NO_REPAIR"] == "1"
    assert "AIDOC_BENCH_NO_REPAIR" not in env_for_config("auto")
```

- [ ] **Step 2–4:** fail → implement (plus the `AIDOC_BENCH_NO_REPAIR` hook in `pipeline.check_document` with a unit test in `tests/unit/test_pipeline.py`: with the env set and a `garbled` scenario the page stays unrepaired and the log line appears) → `uv run --group bench pytest tests/bench -q` pass. **Step 5: Commit** `bench: runner — configs, warm-up, GPU sampling, resumable results, repair-disabled single-engine runs`.

---

### Task 13: Report

**Files:**
- Create: `bench/report.py`, `bench/NONCLAIMS.md` (spec §5 text, Traditional Chinese, verbatim), `tests/fixtures/bench_results_sample.jsonl`, `tests/fixtures/bench_expected_summary.md`, `tests/bench/test_report.py`

**Interfaces:** `report.py --run DIR [--readme-snippet PATH]` → `DIR/summary.md` with: environment line; Tier A table (spec §3.6 columns; mean with worst-sample in parentheses; `—` for configs absent in the run); the `verify` calibration table; Tier C table titled 「不可重現」; `NONCLAIMS.md` appended verbatim under every table group; `--readme-snippet` writes the compact Tier A table + link for README. Numbers formatted with 2 decimals; seconds with 1.

- [ ] **Step 1: Failing test**

```python
# tests/bench/test_report.py
import subprocess, sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2] / "bench"
FIX = Path(__file__).resolve().parents[1] / "fixtures"


def test_report_matches_golden(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    (run / "results.jsonl").write_bytes((FIX / "bench_results_sample.jsonl").read_bytes())
    (run / "env.json").write_text('{"gpu": "RTX 5070 Ti", "driver": "591.86", "date": "2026-10-05", "aidoc_commit": "abc1234", "engines": {"docling": "2.130", "mineru": "4.0.7", "markitdown": "0.1.8"}, "fake": false}', encoding="utf-8")
    assert subprocess.run([sys.executable, str(BENCH / "report.py"), "--run", str(run)], check=False).returncode == 0
    assert (run / "summary.md").read_text(encoding="utf-8") == (FIX / "bench_expected_summary.md").read_text(encoding="utf-8")
    text = (run / "summary.md").read_text(encoding="utf-8")
    assert "不可重現" in text and "不是 OmniDocBench" in text and "| vlm |" in text and "—" in text   # absent config column
```

- [ ] **Step 2–4:** fail → implement → pass (author the golden from the sample results; review it by eye once). **Step 5: Commit** `bench: report generator with fixed tables and the non-claims text`.

---

### Task 14: Calibration gate — does `verify` track ground truth?

**Files:**
- Create: `bench/calibrate.py`, `tests/bench/test_calibrate.py`

**Interfaces:** `calibrate.py --run DIR` reads `results.jsonl` rows of Tier A with `gt_level == "L1"`, computes Spearman ρ between `verify.score` and mean page NED across (config, sample) rows, and page-level precision/recall of `verify` flagged pages (`page_dropped`/`broken_text_layer`/`garbage`) against `gt.defects` ∪ pages with NED < 0.5; runs the three `syn-dropped-pages` Markdown files through `verify_markdown` and records level/exit code; writes `calibration.json` and a Markdown block that `report.py` embeds. Exit code 1 when `rho < 0.6` or `recall < 0.9` — **the benchmark must not be published then** (acceptance #9).

- [ ] **Step 1: Failing test**

```python
# tests/bench/test_calibrate.py
import json
from pathlib import Path
from bench import calibrate as C


def test_calibration_metrics_from_rows():
    rows = [{"tier": "A", "gt_level": "L1", "metrics": {"ned": 0.98}, "verify": {"score": 0.99, "flagged_pages": []}, "gt_defects": [], "low_pages": []},
            {"tier": "A", "gt_level": "L1", "metrics": {"ned": 0.60}, "verify": {"score": 0.70, "flagged_pages": [3, 9]}, "gt_defects": [3, 9], "low_pages": [3, 9]},
            {"tier": "A", "gt_level": "L1", "metrics": {"ned": 0.30}, "verify": {"score": 0.40, "flagged_pages": [1, 2]}, "gt_defects": [1, 2, 5], "low_pages": [1, 2, 5]}]
    out = C.calibrate_rows(rows)
    assert out["rho"] > 0.9 and out["recall"] == 4 / 5 and out["precision"] == 1.0
    assert C.gate(out, min_rho=0.6, min_recall=0.9) is False and C.gate({**out, "recall": 0.95}, 0.6, 0.9) is True


def test_dropped_pages_fixtures_are_judged(tmp_path):
    small = Path(__file__).resolve().parents[2] / "bench" / "corpus" / "small"
    res = C.judge_defect_markdowns(small)             # {name: {"level", "exit_code", "flagged"}}
    assert res["dropped"]["level"] == "warn" and set(res["dropped"]["flagged"]) >= set(json.loads((small / "syn-dropped-pages" / "gt.json").read_text(encoding="utf-8"))["defects"]["dropped_pages"])
    assert res["shifted"]["level"] == "low" and res["swapped"]["level"] == "low" and res["control"]["level"] == "ok"
```

- [ ] **Step 2–4:** fail → implement → pass. **Step 5: Commit** `bench: verify calibration gate (Spearman, page-level P/R, defect fixtures)`.

### Task 15: Docs — README, skill, spec amendments

**Files:**
- Modify: `README.md` (new sections "Verify" and "Benchmark" after "Tests"; License section: corpus licences), `skill/SKILL.md` (step: `aidoc verify out/<stem>/ --json -` when the user doubts a page; explain exit codes), `docs/superpowers/specs/2026-09-24-ai-friendly-doc-design.md` §3/§5/§6/§7/§10 one-line pointers (spec §9), `docs/superpowers/specs/2026-10-01-mcp-server-design.md` §5.2 (two tool rows), `docs/superpowers/specs/2026-10-01-per-page-quality-and-page-map.md` §7 (pointer to `infer_pages`)

- [ ] **Step 1:** write the README sections: `verify` usage (three one-liners: own output dir, third-party Markdown + PDF, CI `--fail-on warn`), what it can and cannot detect (spec §2.7 condensed), the benchmark's purpose, how to run (`uv sync --group bench`, `make_synthetic.py`, `fetch.py`, `run.py`, `report.py`), and a placeholder table that Task 16 replaces. Add the non-claims text under the placeholder.
- [ ] **Step 2:** `uv run pytest tests/unit/test_fixtures_exist.py -q` and any doc-link test the repo has; `git grep -n "aidoc verify" README.md skill/SKILL.md` shows the additions.
- [ ] **Step 3: Commit** `docs: aidoc verify and benchmark sections (README, skill, spec pointers)`.

---

### Task 16: First GPU run, publish, acceptance record

**Files:**
- Create: `docs/benchmarks/<date>.md` (copy of `summary.md` + `env.json` + `results.jsonl`), `docs/benchmarks/README.md` (how to reproduce, one paragraph)
- Modify: `README.md` (replace the placeholder table with the README snippet), spec acceptance record (new §10 in the verify spec), this file's "Implementation notes / deviations"

- [ ] **Step 1: Corpus** — `uv run --group bench python bench/corpus/make_synthetic.py` (full Tier A, record minutes); `uv run --group bench python bench/corpus/fetch.py` (Tier B; record `fetch.status.json` — every `missing` entry is listed in the published report); Tier C samples present via `make_manual.py`.
- [ ] **Step 2: Run** — `uv run --group bench python bench/run.py --corpus A,B --configs auto,auto-norepair,docling,mineru,markitdown --out bench/results/<date>-<host>/` (add `,vlm` when `envs/vlm/.ready` exists — spec §4 dependency); then `--corpus C --configs auto` into the same `--out` with `--resume`. Keep the machine otherwise idle; note any interruption and the `--resume`.
- [ ] **Step 3: Gate** — `uv run --group bench python bench/calibrate.py --run bench/results/<date>-<host>/` → exit 0 required. If it exits 1: stop here, open a follow-up to fix `verify` (thresholds in `verify.py` are the only place to change; re-run Task 3/14 tests), do **not** publish; record the failing numbers in deviations.
- [ ] **Step 4: Publish** — `report.py --run … --readme-snippet /tmp/snippet.md`; copy `summary.md`, `env.json`, `results.jsonl`, `calibration.json` into `docs/benchmarks/<date>/`; paste the snippet into README; verify the non-claims text is present under the README table (`git grep -n "不是 OmniDocBench" README.md`).
- [ ] **Step 5: Verify acceptance #1–#2** on the live library: `uv run aidoc verify out/<ortho book dir>/ --json -` and `… <head first dir>/` — compare `summary.level/reasons/pages_unrepaired` to the sidecars; run `verify` on the raw MinerU `markdown.md` of the ortho book (from `data/work/<task>/seg_*/raw/` if still present, else reconvert one segment with `--engine mineru` and use its raw output) → `confidence ≥ 0.9`, ≤ 30 s. Record numbers.
- [ ] **Step 6: Regression** — `uv run pytest -q`, `uv run --group bench pytest tests/bench -q`, `pnpm --dir web test`, `AIDOC_REQUIRE_MANUAL=1 uv run pytest -m slow -q` (includes Task 8's recorder gate).
- [ ] **Step 7: Commit** `bench: first published run (<date>) + acceptance record` and push.

---

## Coverage table (spec section → tasks)

| Spec § | Content | Tasks |
|---|---|---|
| §1.2 goals 1–3 | verify tool, reproducible benchmark, honesty text | 3–7, 9–13, 13 (NONCLAIMS) |
| §2.1 inputs/options | | 4 |
| §2.2 dialects + inference | | 1, 2, 8 |
| §2.3 checks (reuse + page_dropped + advisories) | | 3 |
| §2.4 report JSON / summary / HTML | | 0, 3, 4, 5 |
| §2.5 exit codes | | 0, 4 |
| §2.6 API + MCP | | 6, 7 |
| §2.7 limitations | README/`--help` | 15 |
| §3.1 principles | | 9–14 |
| §3.2 Tier A corpus | | 9 |
| §3.2 Tier B corpus + licences | | 10 |
| §3.2 Tier C | | 16 (via `tests/manual.py`) |
| §3.3 GT format | | 9, 10 |
| §3.4 metrics | | 11 |
| §3.5 runner (configs, no-repair, warm-up, GPU, resume, env) | | 12 |
| §3.6 tables + publication | | 13, 16 |
| §4 dependency on VLM | `vlm` column only when `.ready` | 12, 16 |
| §5 non-claims | | 13, 15, 16 |
| §6 tests | | 1–8, 9–14 |
| §7 acceptance #1–#10 | | 16 (1,2,8,10), 3/4 (3,4), 5 (5), 6/7 (6), 9/10 (7), 14 (9) |
| §8 risks | mitigations live in 2, 3, 10, 11, 13, 14 | |
| §9 amendments | | 0, 15 |

## Phase acceptance

1. `uv run pytest -q` green with the new unit/API/MCP suites; `uv run --group bench pytest tests/bench -q` green; `pnpm --dir web test` unaffected.
2. `aidoc verify` on the two real books matches their sidecars (acceptance #1); the MinerU-native 1192-page Markdown is inferred with `confidence ≥ 0.9` in ≤ 30 s (#2); the three defect fixtures and the control give exit codes 1/1/1/0 (#3); the scanned-no-markers case exits 3 (#4).
3. Third-party recorded fixtures committed and verified (Task 8), and missing recordings fail in `AIDOC_REQUIRE_MANUAL=1` mode.
4. `bench/corpus/small/` committed with GT; full Tier A regenerates deterministically; `MANIFEST.json` passes `--check` and every committed corpus file carries a licence line; no non-OFL font file in the repo (`git ls-files | grep -i -E "\.(ttf|otf|ttc)$"` → none).
5. One GPU run published under `docs/benchmarks/<date>/` with `env.json`, `results.jsonl`, `calibration.json` (gate passed), README table + non-claims text updated.
6. Deviations section filled (fetch status, timings, calibration numbers, anything skipped).

## Implementation notes / deviations

Implemented 2026-10-12 on top of the VLM engine (`1c167bd`). Each item names the test that pins it.

**Schema.** `verify` writes no database, so the plan needs no schema change; the store stays at v6 (no v7). Pinned:
`tests/api/test_verify.py::test_doc_id_form_uses_library_document` (the row is unchanged after a verify).

**Systemic fix found by verify (beyond the plan).** On the real books `verify` reported `page_dropped` pages that our
own conversions had marked `ok`: 13 in the orthopaedic book (MinerU left out full-page tables and a chapter opening),
51 in Head First Android (Docling turned text pages into pictures), and on the benchmark sample
`syn-broken-tounicode-c` Docling returned empty sections for 9 of 30 pages with quality `ok`. A silent loss is exactly
what the per-page model exists to catch, so `page_dropped` moved from `verify.py` into `quality.assess` /
`quality.dropped_pages` and `pipeline.check_document`: it is flagged like a failed page, repaired per page, counted in
the 20 % `pages_flagged` rule, and `warn` when unrepaired. `PAGE_CHECK_VERSION` 1 → 2 so the server re-grades existing
documents once. Pinned: `tests/unit/test_pipeline.py::test_silently_dropped_page_is_detected_and_repaired`,
`::test_unrepairable_dropped_page_is_warn_never_silent`, `::test_reported_failed_page_is_not_also_dropped`,
`tests/unit/test_quality.py::test_assess_flags_a_silently_dropped_page_as_warn`. Consequences:
- a single dropped page is page-level `warn` (review focus 3 holds), but more than 20 % dropped pages make the document
  `low` like any other flagged page: `tests/unit/test_verify.py::test_many_dropped_pages_make_the_document_low_like_any_flagged_page`.
  The small negative (`syn-dropped-pages`, 1 of 3 pages) is therefore `low`; the full one (5 of 29) is `warn`
  (`bench/calibrate.py::expected`, `tests/bench/test_calibrate.py::test_dropped_pages_fixtures_are_judged`);
- the plan's `test_dropped_pages_are_page_level_warn_not_low` (2 of 7 pages) became two tests (1 page → warn, 2 → low);
- `test_pipeline_segments.py::test_last_engine_quick_check_does_not_fail_fast` now allows the per-page repair call the
  "low" fake segment triggers (its page 1 is a 5-character section with a text layer);
- the web document view names `page_dropped` and `page_conversion_failed` pages (the latter had no label before):
  `web/src/features/document/__tests__/pageWarnings.test.ts`.

**Task 1 (markers).** pdfmux does not document a Markdown page-marker format (README and repository checked
2026-10-12); `<!-- page N -->` is implemented as the plan states and is unverified against pdfmux output.
`tests/unit/test_markers.py`.

**Task 2 (infer_pages).** Additions to the spec §2.2 algorithm, all in `tests/unit/test_infer_pages.py`: image links and
HTML comments are blanked before squashing (asset paths would squash to letters); broken-font pages are excluded like
in the spot check (`exclude=`); the monotone filter breaks ties by rank displacement, so a swapped page is dropped and
the pages between the swapped ones are kept; a boundary is placed at the paragraph holding the first located window
of the next page's layer (and after the last located window of the previous page), else at the midpoint of the start /
end estimates (cancels dropped running headers / footers), snapped to a paragraph start; `dropped_candidates` include
out-of-order pages. Measured on the real books with the markers stripped (MinerU output of the orthopaedic book,
1,192 pages): confidence 0.976 (1155 / 1184 decidable), boundaries within one paragraph of the true marker on 59 % of
anchored pages (28 % exact), the rest further off where the PDF text-layer order differs from MinerU's reading order
(side boxes, figure captions); Head First Android (Docling): confidence 0.78, 66 % within one paragraph. The spec's
"≤ 1 paragraph" holds on the fixtures, not on every real page — README "Verify" states the limitation.

**Task 3 (verify).** `quality.assess(..., skip_page_map_reasons=)` as planned; `probe.page_features()` uses
`get_cdrawings()` with shared `_is_math` / `_grid_from_drawings` helpers (identical results to `_analyse_page` on all
234 fixture and manual pages checked) and one PDF pass reads layers and features: 1,192 pages in 13.5 s with markers,
21.5 s wall through the CLI when the page map is inferred. Test changes against the plan: `text.pdf` has ruled table
lines, so the document rule `missing_table` makes it `low` (spec §2.3); the advisory test asserts the advisory and that
the level is the document rule's (`test_table_advisory_on_text_pdf_page_2_when_table_missing`). PyMuPDF's raw layer of
`broken_tounicode.pdf` page 1 is only 4.2 % garbage (below `PAGE_GARBAGE_MAX`), so the "third-party output" in
`test_broken_text_layer_page_is_flagged_without_repair_info` carries U+FFFD between characters as Docling's pdfium
backend emits. `input` gains `markers_applied` / `markers_reason`; an inferred `page_map` gains `undecidable` and
`out_of_order`. HTML rendering is `verify_report.render_html()` (no `VerifyReport.to_html`, keeping `models.py` free of
rendering code).

**Task 4 (CLI).** No PDF and no `--pages` → exit 3 as spec §2.5 says (`verify_markdown` raises `NeedsPagesError`, a
`ValueError`, so the plan's unit test holds): `test_exit_codes`. The subprocess test uses `paged_furniture.pdf`
(`text.pdf` is `low` → exit 1). The directory form finds the work copy through a read-only SQLite connection
(`mode=ro`), never through `Store`.

**Task 6 (API).** The body is spooled with a cap of 2 × `upload_max_bytes` + 1 MiB before multipart parsing, then each
file is held to `upload_max_bytes` (413 `too_large`); a Library document whose output is gone → 409 `output_missing`;
the report echoes the upload names, never server temp paths. `tests/api/test_verify.py`.

**Task 7 (MCP).** `VerifyOut.page_map` is a new `VerifyPageMap` (method, coverage, alignment, anchored, decidable,
confidence): `PageMapBrief`'s exact shape is pinned by the `get_document_info` tests. Unreadable PDF →
`unsupported_file`. `tests/mcp/test_tool_verify.py`.

**Task 8 (recorded outputs).** The recorder compares an existing recording with a fresh one (engine drift fails the
slow test; `AIDOC_RECORD=1` re-records). Docling 2.130 drops the blank page 4 (6 placeholders, 7 sections for 8 pages
→ inferred). `tests/integration/test_record_third_party.py`, `tests/unit/test_verify_formats.py`.

**Task 9 (corpus).** No Augraphy / img2pdf: scans are degraded with Pillow + numpy, seeded (noise, ±1.5° skew, JPEG
q55, ink bleed, fold), and written with PyMuPDF — reproducible without Augraphy version drift. Fonts: Noto Sans CJK TC
only (no Serif); the used glyphs are subset with fontTools and converted to TrueType, because a subset CID-keyed CFF
renders blank in MuPDF. Gutenberg texts are not committed: `SOURCES.json` pins `files/<id>/<id>-0.txt` by sha256
(the `cache/epub` copies of the Chinese books are mojibake); English books are #1342 and #11 (#84 was unavailable).
Full-size `gt.json` files are not committed (deterministic regeneration in about 100 s; the small corpus GT is
committed and compared byte for byte by `tests/bench/test_make_synthetic.py`). The small corpus is 2.37 MB (the plan
said ≤ 2 MB; the two scans are 1.3 MB). The `bench` dependency group (rapidfuzz, matplotlib) is installed by default
(`[tool.uv] default-groups`) so restarting the server with `uv run` never strips it. Book sizes per spec; tables /
formulas / two-column items are 10 pages each.

**Task 10 (Tier B).** Nine documents (2 Executive Yuan + 2 Legislative Yuan gazettes, 3 arXiv CC BY 4.0, 2 NIST),
licences checked 2026-10-12; ppg.ly.gov.tw needs a browser User-Agent. Not sourced: the Internet Archive public-domain
Traditional Chinese scans and the OmniDocBench / olmOCR-Bench sanity entries. Government pages were scanned for ID
numbers, e-mail addresses and phone numbers: only ministry office numbers, no personal data, no `drop_pages`.

**Task 11 (metrics).** TEDS is in-repo (Zhang–Shasha); no third-party TEDS package. `plain()` strips HTML tags as well
(MinerU / Docling emit tables as HTML; found in the real-engine smoke run): `test_plain_strips_html_tables_like_gfm`.

**Task 12 (runner).** Conversions run as `<venv python> -m aidoc.cli convert` (not `uv run`), each config with its own
`aidoc.toml` (`AIDOC_CONFIG`), data dir and output root. `seconds_excl_startup` = wall time minus the warm-up wall time
of the 3-page sample on the engine used (an approximation). GPU memory is the whole GPU (`nvidia-smi`); the report
shows the increase over the pre-conversion baseline. The `vlm` config accepts a loopback endpoint only and was run on
the small corpus. Raw configs (`raw-docling`, `raw-markitdown`, `raw-mineru`) exist but were not part of the published
run.

**Task 13 (report).** The non-claims text follows spec §5 except where the implementation differs: item 2 (no
Augraphy, no Tier B real scans), item 4 (states that single-engine and auto-norepair columns run without repair),
item 7 (local VLM, no cost).

**Task 14 (calibration).** A page counts as bad when its NED < 0.5 or it is a defect page the output did not recover
(NED < 0.90); a repaired broken page is not a miss (`test_recovered_defect_pages_are_not_misses`).

**Repair acceptance (benchmark finding).** On the orthopaedic book a `page_dropped` page (912) was marked
`repaired_by` with a few characters of OCR text while `verify` still saw it as dropped. `repair_pages(min_chars=)`:
a dropped page needs `DROPPED_SECTION_MAX` characters to count as repaired.
`tests/unit/test_repair.py::test_a_dropped_page_needs_a_real_section_to_count_as_repaired`,
`::test_check_document_asks_dropped_pages_for_a_real_section`. A digital PDF whose Markdown shares no text with it is
reported as "no Markdown text matches the text layer", not as a scan
(`tests/unit/test_verify.py::test_unverifiable_says_why_when_the_pdf_has_text`). The bench `raw-mineru` script needed a
`__main__` guard (MinerU's process pool re-imports it on Windows). Tier C samples take their page count from the PDF
(`tests/bench/test_run_fake.py::test_tier_c_sample_without_gt_takes_its_page_count_from_the_pdf`); report rows of the
small corpus get their own table (`tests/bench/test_report.py::test_small_corpus_rows_get_their_own_table`).
The synthetic broken font's glyph ids are shifted by 31 (not the 29 of Head First's font): those pages are caught by
U+FFFD, not by `shift_hits`.

**Task 16 (GPU run, gate, publication).** Runs (not committed; `bench/results/` is gitignored):
`bench/results/2026-10-12-ulove-arrangement/` — Tier A + B × auto, auto-norepair, docling, mineru, markitdown on the
code before the page_dropped fix (20:07–21:59, 1 h 52 min, 187 conversions, 0 failures); and
`…-pagedrop/` — `auto` on A + B after the fix, Tier C `auto` (orthopaedic book 781 s, Head First 3268 s), `vlm` on the
small corpus (local Ollama `gemma4:12b`: nearly every page `truncated` at `max_output_tokens` 4096, about 66 s per page —
not a usable column). Calibration gate (Task 14) **failed**: ρ 0.48, page recall 0.16 (precision 1.00) on the base run;
ρ 0.67, recall 0.25 on the post-fix run — **corrected 2026-10-13**: that run has different rows (40: `auto` plus 11 small-corpus `vlm` rows, whose ρ is 0.82); `auto` alone went from ρ 0.11 to ρ −0.10, so it was no improvement. Diagnostic variants (low documents flag every page, spec-only defect pages,
text-layer samples only) all stay below the gate, so the failure is not a matter of definition. As Step 3 says, nothing
was published: no `docs/benchmarks/`, no README table (README "Benchmark" says why). The numbers, the reasons for the
misses and the routing findings the run exposed are in the spec acceptance record (§10). Follow-ups: page-level
presentation of whole-document `low`, an OCR-confidence signal for scans, page attribution of cross-page paragraphs;
routing (Traditional Chinese scans → Docling + RapidOCR scored 0.96 vs MinerU 0.68; formulas → MinerU); the
ruling-line heuristic that read stacked figure boxes as a table; the VLM output limit.

**Regression (2026-10-13).** `uv run pytest -m "not slow"`: see the final report; the one skip is the pre-existing
`test_tool_convert_path.py` symlink test (no symlink privilege on this host). `pnpm --dir web test`: one timing-sensitive
ConvertPage test failed once while the Python suite ran in parallel and passed on rerun.

**Slow suite change.** `tests/integration/test_e2e_fixtures.py` expected `twocol.pdf` to end on Docling. Docling returns an
empty section for its page 2 (the page repeats page 1's text); with `page_dropped` that is 1 of 2 pages flagged, over
20 %, so the conversion now falls back to MinerU. The test asserts that (Docling tried first, `pages_flagged`, MinerU
final).

**VLM budget test.** `tests/reliability/test_vlm.py::test_page_cap_and_daily_budget`: the fake "low" output of each
40-page segment is a 5-character page, now `page_dropped`; the other engines' repairs return 5 characters again (not a
real section), so the VLM repairs pages 1 and 41 and uses 2 of the 5 budget pages. The test asserts that and counts the
later repairs on top of it.

**Verifier round (2026-10-13).** Fixes, each pinned by a test that failed first:
1. Repair acceptance by coverage: `repair.accept_repaired_section(layer=)` needs ≥ `PARTIAL_COVERAGE_MIN` (0.5)
   bigram-multiset coverage of the page's prose layer — `tests/unit/test_repair.py::test_a_dropped_page_repair_must_cover_its_text_layer`.
2. No duplicated OCR copies: `pagemap.page_homes()` locates every page's unique probes; `quality.page_checks()` gives
   `page_misaligned` (all found probes elsewhere, ≥ 2) and `page_oversized` (holds another page's text, or > 2 × its
   layer + 300); misaligned / oversized pages are never sent to repair —
   `test_page_checks.py::test_misplaced_page_is_misaligned_not_dropped`,
   `test_pipeline.py::test_misplaced_page_is_flagged_never_copied_twice` (fake engine `misplace`, verifier's case),
   `test_repair.py::test_check_document_repairs_dropped_and_partial_pages_against_their_layer`.
3. `reassess` returns without stamping when there is no PDF — `test_reassess.py::test_a_document_without_its_pdf_is_not_stamped_as_checked`.
4. History carried forward (sidecar first, then DB): `test_reassess_keeps_repaired_pages_failures_and_method`,
   `test_history_wiped_by_an_earlier_regrade_is_recovered_from_the_sidecar`; `PAGE_CHECK_VERSION` 3. Re-grades use
   `last_resort=True` (only unrepaired pages count for the 20 % rule).
5. `page_misaligned` per page and `verify` checks every page by default (function, CLI, API) —
   `test_verify.py::test_every_page_is_the_default_in_the_function_and_the_cli`; bench negatives
   `swapped_content.md` and `partial.md` (generator v2) — `tests/bench/test_calibrate.py::test_swapped_content_and_partial_negatives_are_caught`.
   They are reported as `extra_negatives_ok` next to the gate; the gate's definition and thresholds are unchanged.
6. `page_partial` (coverage < 0.5) in conversions and verify — `test_page_checks.py::test_partial_page_is_flagged_with_its_coverage`.
   Prose layers (`pagemap.content_layers(prose_only=True)`): running header / footer and page numbers removed (edge
   lines repeating on ≥ 20 % of pages, never more than half a page), and lines under 20 squashed characters (figure
   labels, table cells) left out; this also fixes the figure-page `page_dropped` false positive —
   `test_figure_page_with_only_header_labels_and_caption_is_not_dropped`. Measured on the two real books before
   reconversion: orthopaedic 12 dropped / 6 partial / 1 misaligned / 4 oversized, Head First 50 / 33 / 3 / 1; the
   partial pages are mostly diagram pages whose long labels are prose-length lines (the engine kept the diagram as a
   picture) and code pages whose text layer differs from what is drawn.
Minor: custom marker regex limited to 200 characters, its group must always take part and match something, nested
unbounded quantifiers are refused (`tests/unit/test_markers.py`, API 422 in `tests/api/test_verify.py`). `aidoc serve
--help` and the README say to pass the token through `AIDOC_TOKEN` / `aidoc.toml`, not `--token`.

Other changes forced by the new checks: the fake engine writes the whole page text (it wrote 400 characters, which
is now `page_partial`); `test_vlm_low_overlap_is_advisory_only` passes an empty prose layer to isolate the advisory;
the reliability VLM tests give the fake endpoint each page's own text (`pages`), and the budget test now sees 1 VLM
page (only one of the two 5-character pages has a prose layer). The bench runner gained `--rescore` (re-score rows
from the outputs on disk) and records `flagged_all`; the calibration report adds diagnostics (all page reasons,
verifiable rows) for information only. Live DB: backup `data/aidoc.db.bak-20261013-023339`, dry run on a copy, then
the two books' `source_path` set to their sha-verified originals and the server restarted (v3 re-grade). Numbers in
the spec acceptance record §10.3.

**Verifier round 2 (2026-10-20).** Each fix pinned by a test that failed first; numbers in the spec §10.4.
- *Labelled real pages* (`tests/fixtures/page_labels.json`, scorer `bench/page_labels.py`, gate
  `tests/integration/test_page_labels_real.py`, slow, fails instead of skipping under `AIDOC_REQUIRE_MANUAL=1`):
  293 judged pages of the two books and five raw-engine samples (`manual_samples.json`), identity by sha256 only,
  no document text. Protocol: the verifier's 21 pages plus a seeded sample were judged before any check changed;
  that first test split was measured with the round-1 checks (page_dropped P 0.91 / R 0.83, page_partial 0.86 /
  0.67, page_misaligned 1.0 / 0.5, page_oversized 0 / 0) and once with the first fix (0.80 / 1.0, 0.86 / 0.67,
  1.0 / 0.5, 1.0 / 0.5: the misses showed the tag-stripping bug and doc-wide broken fonts skipping Head First pages),
  then a second split once with the new checks frozen
  (oversized P 0.0 on 11 flags: a paragraph continued across the page break put two probes of the next page in the
  section; the OCR of screenshots made sections 3× their layer; doc-wide broken-font names cut real text out of Head
  First layers), then a third split after those fixes, frozen again. Each measured split became design data ("dev").
  Label corrections, each recorded in the label's `note`: five Head First pages judged partial with an aid that
  stripped `<XML>` code as HTML tags (the same bug as finding B, in my judging script); Ortho 779 / 874 by the written
  policy (a text-heavy diagram kept as a picture is dropped, like 369 / 454); hfmix 10 (holds page 12's paragraph)
  and Ortho 1129 (holds page 1130's whole table, past the cut of the judging view) after the check flagged them.
  Thresholds were set on dev pages only (`quality.py` constants) and not changed after the third split was measured.
  The gate requires P and R ≥ 0.9 per reason on all labels; it fails for page_partial (P 0.88: two borderline code
  pages whose callouts count as prose) and page_oversized (R 0.86: hfmix3 28 holds 0.30 of page 29, the threshold is
  0.30 of the distinctive text) — reported, not tuned.
- *A, dropped on the whole text*: `pagemap.pdf_layers` returns `PageLayers(full, prose, body, untrusted)`; dropped
  when the prose layer has ≥ 120 or the furniture-free body layer ≥ 300 chars and the section is empty or a heading /
  caption only (< 0.2 coverage in < 150 chars) — `test_page_checks_round2.py::test_full_page_table_lost_to_a_picture_is_dropped`,
  `::test_heading_only_section_is_dropped_not_partial`. Partial stays on the prose layer. Coverage: character
  5-gram multisets (`COVER_N`), also for repair acceptance (a repair layer is the body layer for a page without prose).
- *B, merged tables / foreign text*: only engine-emitted HTML tags, entities and link URLs are stripped
  (`pagemap.strip_tags`; generic tag stripping ate XML code — `::test_xml_code_listing_is_page_text_not_html`).
  "Elsewhere" is measured on the page's distinctive 5-grams (on no other page within 8) that another section within 4
  holds beyond its own layer: ≥ 0.5 (or own + elsewhere ≥ 0.8, or an empty section and ≥ 0.3) → page_misaligned, never
  repaired; the host page_oversized, also on a dropped / partial page, which then is not repaired —
  `::test_table_merged_into_previous_page_is_misaligned_not_dropped`, `::test_page_of_repeated_sentences_moved_to_a_neighbour_is_misaligned`,
  `::test_one_shared_sentence_does_not_make_a_host`, `::test_solution_page_repeating_the_exercise_is_not_oversized`,
  `::test_near_empty_page_holding_another_pages_text_is_oversized`. Deviation from the request "≥ 2 matching probes
  before a page counts as hosting": probes no longer decide hosting at all (two probes of a continued paragraph were
  the second split's false positives); they still decide page_misaligned for the moved page. The size proxy
  (> 2 × layer + 300) is removed for the same reason (OCR of screenshots); `OVERSIZE_RATIO` / `OVERSIZE_SLACK` gone.
- *Broken fonts*: `probe.broken_fonts_by_page`; a page's layer leaves out spans of the fonts broken on that page (Head
  First: 339 of 532 pages use a broken handwriting font, their body text extracts fine); `untrusted` only with
  < 120 chars of healthy text — `::test_broken_font_spans_are_left_out_of_the_layer`.
- *C, ReDoS*: the custom marker is a fixed template (literal text around `{page}` or one `(\d+)` / `([0-9]+)` /
  `(\d{1,k})`, escaped punctuation, `\s` `\s*` `\s+`, starting with a literal) compiled to a linear pattern; the five
  bypasses are refused at once (`tests/unit/test_markers.py`, API 422 `tests/api/test_verify.py::test_redos_bypasses_are_422_fast`).
  The structural regex check is deleted.
- *D*: a repair (also from history on a re-grade) whose section still fails the checks is `repair_rejected` and
  unrepaired — `::test_regrade_rechecks_a_page_with_an_old_repair`; furniture is judged per document (more than half
  of the document's text → none) — `::test_furniture_only_and_caption_only_pages_are_not_dropped` (the edge3.pdf
  shapes; the verifier's real edge3 Docling output now flags only 5 oversized / 7 misaligned, which is right);
  link URLs do not count — `::test_link_urls_do_not_count_toward_the_section_size` (GEO report p14: no flag);
  a scanned PDF with markers is `verifiable: "partial"` — `::test_scan_with_markers_is_partially_verifiable`.
- *Gate input correction*: `bench/run.py` `FLAG_REASONS` now holds every page-level reason (it lacked page_partial /
  page_misaligned / page_oversized) — `tests/bench/test_run_fake.py::test_flag_reasons_cover_every_page_level_reason`.
  Gate definition and thresholds unchanged.
- Tests changed by the new definitions: `test_partial_page_is_flagged_with_its_coverage` and the verify cut-page test
  cut to 30 % (15 % of a short page in < 150 chars is now dropped); `test_dropped_pages_helper` uses the page's own
  text for the other sections (30 × "x" is now dropped); the VLM budget test sees 2 dropped pages (was 1).
- `PAGE_CHECK_VERSION` 4: the server re-grades every PDF document with its PDF on start.
- Correction of the round-1 record: "Head First: 55 repaired_by restored from the sidecar" was true for the re-grade
  of 2026-10-13, but the MinerU reconversion that followed replaced that conversion; the document has 15 repaired
  pages (Docling → MinerU fallback, 12 broken-text-layer + 3 partial repairs).
- A repair must cover the layer its page's check used (body for a dropped page, prose for a partial one): the first
  reconversion accepted Ortho p779 against the prose layer and the final check rejected it —
  `tests/unit/test_repair.py::test_a_dropped_page_is_repaired_against_its_body_layer_a_partial_one_against_its_prose`.
  Both books were reconverted again after this fix (numbers in the spec §10.4: 0 rejected repairs).
- The five label samples carry `"role": "labels"` in `manual_samples.json`; `test_manual_fixtures.py` does not convert
  them (each took longer than the 300 s test timeout and killed the slow run; they are checked against their judged
  Markdown by `test_page_labels_real.py`).
- Final matrix: not-slow 1246 passed, 1 skipped (symlink privilege); slow 68 passed, 2 failed (the label gate:
  page_partial precision, page_oversized recall), 0 skipped; web 211 passed; build ok; e2e 11 passed.

**Decision (2026-10-20, approved by the user; recorded before any re-run): calibration gate redefined** (spec §3.4).
Computed only on rows and pages verify can judge (rows with a text layer: `verifiable is True` and not a scan; pages
with ≥ 20 squashed text-layer chars); ρ ≥ 0.6 unchanged; page recall ≥ 0.9 and page precision ≥ 0.9 over every
page-level reason, advisory `page_partial` excluded; scans reported separately, not gating; validated once on a
fresh Tier A seed (never generated before) plus Tier B (L3 only: no page GT, flag counts as information); no tuning
after the result. The `syn-dropped-pages` negatives still have to pass. Routing unchanged (user, 2026-10-20).

**Verifier round 3 (2026-10-20).** Each fix pinned by a test that failed first:
- *Page-text policy* (user confirmed 2026-10-20): for page_partial, code listings, tables and body text count as page
  text; callouts and labels inside a figure kept as a picture do not. Checker: lines whose centre lies in an
  embedded picture (≥ 2 % of the page) leave the prose layer unless monospaced (Head First draws code over a
  background picture) — `test_page_checks_round2.py::test_callouts_on_a_picture_are_not_page_prose`,
  `::test_code_over_a_background_picture_stays_page_text`. Limit (documented, spec §11): vector-drawn diagram labels
  and short lines (table cells) are not told apart. Relabelled with a note: hfmix3 26 (now ambiguous).
- *page_partial advisory*: in `advisories`, never a reason; not counted as flagged / unrepaired, not repaired, no
  level change, no fallback — `::test_page_partial_is_an_advisory_that_never_raises_the_level`,
  `::test_check_document_does_not_repair_an_advisory_page`; web info note (`pageWarnings.test.ts`); the bench cut-page
  negative checks the advisory (`tests/bench/test_calibrate.py`).
- *Wrong-Unicode-map guard*: `quality.language_gap` / `untrusted_layers` —
  `::test_shifted_cipher_text_layer_is_untrusted_not_partial`. The synthetic broken samples: 34 false page_partial
  → 1 (MinerU), 0 in the other configs.
- *Broken-font classification*: symbol / dingbat fonts excluded, per page by broken-character share ≥ 2 %, span
  names matched normalised, a name embedded broken and healthy counts a span only with ≥ 10 % unmapped characters —
  `tests/unit/test_probe.py::test_symbol_and_dingbat_fonts_are_not_broken_fonts`,
  `::test_broken_tounicode_fonts_detected` (page 2's one-character footer no longer makes it a broken-font page),
  `tests/bench/test_make_synthetic.py` (variant c: footer only → no broken-font page). Head First: 339 → 124 pages.
  `probe.broken_font_pages` is the corrected list, so alignment (spot check exclusion), flag_pages and reports use it.
- *ReDoS*: adjacent whitespace tokens merge into one `[ \t]{m,}` run — `tests/unit/test_markers_fuzz.py` (the two
  bypasses, a seeded 300-template fuzz with a 0.5 s per-call bound).
- *Label gate*: `tests/integration/test_page_labels_real.py` asserts only on split `heldout_v2` (Wilson 95 % lower
  bounds: precision ≥ 0.8; recall ≥ 0.8 with ≥ 10 positives); every earlier split is `dev`; `heldout_v2` is empty
  with its protocol in `page_labels.json` "splits" — the independent verifier labels it blind. Until then the test
  fails under `AIDOC_REQUIRE_MANUAL=1` by design.
- *Calibration gate*: redefined by the user's decision above; `bench/calibrate.py::gate_v2`; the old numbers are kept
  as `legacy` information. Fresh Tier A: `make_synthetic.py --seed-salt "fresh-20261020:" --out
  bench/results/corpus-fresh-20261020` (seeds never generated before), `run.py --tier-a-root`.
- `PAGE_CHECK_VERSION` 5 (re-grade on start).

**Frozen thresholds (2026-10-20, before the held-out labels and the fresh-seed gate run; not to be changed on their
results):** quality.py — `DROPPED_LAYER_MIN` 120, `DROPPED_BODY_MIN` 300, `DROPPED_SECTION_MAX` 20,
`DROPPED_COVERAGE_MAX` 0.2, `DROPPED_TEXT_MAX` 150, `COVER_N` 5, `PARTIAL_COVERAGE_MIN` 0.5, `ELSEWHERE_MIN` 0.5,
`SPLIT_PRESENT_MIN` 0.8, `MOVED_MIN` 0.3, `NEIGHBOUR_PAGES` 4, `DISTINCT_PAGES` 8, `DISTINCT_MIN` 40, `GIB_GAP` 1.5,
`GIB_LAYER_MIN` 120, `GIB_SECTION_MIN` 60, `GIB_VOCAB` 1600, `MISALIGNED_MIN_PROBES` 2, `FLAGGED_MAX_RATIO` 0.20;
pagemap.py — `FURNITURE_EDGE_LINES` 3, `FURNITURE_MIN_SHARE` 0.2, `FURNITURE_MAX_SHARE` 0.5, `PROSE_LINE_MIN` 20,
`TRUSTED_LAYER_MIN` 120, `FIGURE_AREA_MIN` 0.02; probe.py — `BROKEN_SHARE_MIN` 0.02, `SYMBOL_FONT` pattern, mixed
fonts 10 % unmapped; label gate — `LB_MIN` 0.8, `RECALL_MIN_POS` 10; calibration gate — ρ 0.6, page P / R 0.9.
- *Regression found by the slow suite after the reconversion*: the share rule hid Head First T-001 pages whose
  only broken text is a heading (0.5-1 % of the page): `ProbeResult.font_suspect_pages` (every page using a broken
  non-symbol font) feeds `flag_pages`, which still needs garbage in the Markdown —
  `tests/unit/test_probe.py::test_font_suspect_pages_keep_small_broken_shares_for_the_markdown_check`,
  slow `test_t001_mix_detection_and_repair`. Head First reconverted again after it.
- Final matrix: not-slow 1264 passed, 1 skipped (symlink privilege); slow 66 passed, 4 failed (the held-out label
  gate, by design until heldout_v2 is labelled), 0 skipped; web 212 passed; build ok; e2e 10 passed, 1 failed (above).
- Fresh-seed gate run (once): rho 0.63, page precision 0.83, recall 0.72 on 102 text-layer rows: not passed; not
  published, nothing tuned (spec §10.5).
- e2e `mcp.spec.ts` "貼上 token ... nothing stored" fails at its last step (after navigating to Library and back the
  pasted value is still in the box) also with the web code of 30d8831: environmental (browser back-forward cache),
  not caused by this round; left as is and reported.
