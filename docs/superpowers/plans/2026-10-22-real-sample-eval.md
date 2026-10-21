# Real-Sample Evaluation (zh scan route, formula route, local VLM re-test) — Implementation Plan

> **For agentic workers (Opus implementer):** REQUIRED SUB-SKILL: superpowers:executing-plans or superpowers:subagent-driven-development, task by task, test first. Read the spec `docs/superpowers/specs/2026-10-22-real-sample-eval-design.md` in full before Task 0 — its §5 (normalisation and metrics), §6 (ground-truth quality) and §7 (pre-registered decision rules) are the contract; where this plan and the spec disagree, the spec wins and the plan is fixed. Tasks 0–8 build and dry-run the harness on synthetic fixtures only. Task 9 is the verifier's ground-truth preparation. Task 10 is the one registered run. Task 11 records. **The real samples under `tests/fixtures/manual/realeval/` are opened by the harness exactly once, in Task 10.**

**Goal:** a small harness under `bench/realeval/` that (a) scores the 12 real documents (6 Traditional Chinese image-only scans with page-aligned Wikisource GT, 4 CC BY arXiv formula papers with document-level LaTeX GT, 2 English OCR-layer scans as controls) against `auto`, `docling` and `mineru`, (b) measures the local PaddleOCR-VL-1.6 backend over a loopback `llama-server` exactly as spike S1 did, (c) applies the spec's pre-registered rules mechanically and writes `decisions.json`, and (d) produces a committed record with numbers only. The harness decides nothing by itself and changes nothing in `src/aidoc/`.

**Spec:** `docs/superpowers/specs/2026-10-22-real-sample-eval-design.md` (Traditional Chinese). Related: verify/benchmark spec `2026-10-05-verify-and-benchmark-design.md` (§3.4 metrics, §10.2 the synthetic routing signal), VLM spec `2026-10-05-vlm-ocr-engine-design.md` (§3.4 rule 1, §5 routing, §13 #6), spike `docs/superpowers/spikes/2026-10-vlm-ocr.md` (pinned artefacts and the measurement method), `bench/run.py` / `bench/metrics.py` / `bench/gpu_sampler.py` (reused, not copied).

**Architecture:** `bench/realeval/` — `MANIFEST.json` (identities only), `samples.py` (load, sha256, gitignore guard, control rendering), `normalize.py` (spec §5.1 policy, errata), `scoring.py` (page alignment, NED/CER/bag-of-chars, paired bootstrap, per-document summaries), `formulas.py` (document-level LaTeX recovery, HTML text guard), `vlm_measure.py` (loopback `llama-server` requests, recall, timing, VRAM, cover phrase), `rules.py` (the §7 constants and `decide()`), `gt_quality.py` (spot-check draw, Wilson bound, margin), `run.py` (orchestration; reuses `bench.run.convert`, `write_config_files`, `env_for_config`, `_timeout`, `_check_vlm_endpoint`), `report.py` (`summary.md` and the record skeleton). Tests in `tests/unit/test_realeval_*.py` use synthetic fixtures and the fake VLM endpoint only.

**Tech stack:** Python 3.10 host, PyMuPDF, rapidfuzz (already a bench dependency), stdlib `random`/`statistics`/`html.parser`/`urllib`; no scipy, no opencc (optional diagnostic only, Apache-2.0, auto-detected). `uv run` for everything. GPU parts run manually on the dev host; CI runs the unit tests and the `--fake` dry run.

## Global Constraints

- **No change under `src/aidoc/` or `web/`** in any task of this plan. The decisions it produces trigger separate plans (spec §7.1/§7.2/§7.3.3). A test (Task 0) does not enforce this — the verifier's checklist does (`git diff --stat`).
- **Never open the real samples before Task 10.** `samples.load()` refuses when `--fake` is set, when any file under the realeval folder is tracked by git, or when a sha256 differs. Unit tests build stand-in sample folders in `tmp_path` from `tests/fixtures/` files.
- **Constants live in `bench/realeval/rules.py` and nowhere else**; `tests/unit/test_realeval_rules.py::test_constants_match_spec` pins every number to the spec's §7 table. Changing a constant after the freeze commit is a spec change and voids the run.
- **Privacy (spec §9):** the only network endpoint the harness will talk to is a loopback `llama-server`; `run.py` calls `bench.run._check_vlm_endpoint()` and writes `[vlm] backend = "none"` into every `aidoc-<config>.toml`. No telemetry, no uploads, no `--vlm-base-url` outside loopback.
- **Committed artefacts carry no document text**: `MANIFEST.json`, `variants.json`, `rules.py`, the record. `report.py` has a test that a 40-character GT sentence never appears in its output. Snippets ≤ 30 characters are allowed in the record only with Wikisource attribution.
- `bench/` is outside the wheel (hatch `packages = ["src/aidoc"]`).
- Commit trailer for every commit (both lines):
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0175gLq9uam6M5Z9yNNxM4Ba
  ```

## Review Focus

1. `normalize.norm_text()` is one function used for GT and prediction alike; the only GT-only step is errata application (spec §5.1 order).
2. Pages without GT are excluded; GT pages without a prediction score 0; a failed conversion scores 0 on every GT page and is listed (spec §4.2, §3).
3. The paired bootstrap resamples **pages** with a fixed seed; the document-level win count is computed separately; `decide()` requires both (spec §5.3, §7.1).
4. Formula GT = `display == "block"` only; the pre-registered `norm_latex_doc()` list is exactly the spec §5.5 list, nothing more.
5. The VLM request shape is the spike's (`"OCR:"`, 200 dpi, long side ≤ 1400, `max_tokens 4096`, `temperature 0`, one page per request, no concurrency, no retry).
6. `decide()` is pure: `results -> decisions`; every rule's inputs and booleans appear in `decisions.json` so the verifier can recompute by hand.
7. The margin (0.05 / 0.10) is read from `gt_quality.json`, which exists before any conversion.
8. The `--fake` dry run exercises every file writer and never touches the real folder.

## File map

| Path | Task |
|---|---|
| `bench/realeval/__init__.py`, `bench/realeval/rules.py`, `tests/unit/test_realeval_rules.py` (constants part) | 0 |
| `bench/realeval/MANIFEST.json`, `bench/realeval/samples.py`, `tests/unit/test_realeval_samples.py` | 1 |
| `bench/realeval/normalize.py`, `bench/realeval/variants.json`, `tests/unit/test_realeval_normalize.py` | 2 |
| `bench/realeval/scoring.py`, `tests/unit/test_realeval_scoring.py` | 3 |
| `bench/realeval/formulas.py`, `tests/unit/test_realeval_formulas.py` | 4 |
| `bench/realeval/vlm_measure.py`, `tests/unit/test_realeval_vlm.py` | 5 |
| `bench/realeval/rules.py` (`decide()`), `tests/unit/test_realeval_rules.py` (decision part) | 6 |
| `bench/realeval/report.py`, `tests/unit/test_realeval_report.py` | 7 |
| `bench/realeval/run.py`, `bench/realeval/gt_quality.py`, `tests/unit/test_realeval_run.py`, `tests/unit/test_realeval_gt_quality.py` | 8 |
| `tests/fixtures/manual/realeval/gt_errata.json`, `gt_quality.json` (gitignored; verifier) | 9 |
| `bench/results/<date>-realeval/` (gitignored) | 10 |
| `docs/superpowers/evals/2026-10-22-real-sample-eval-record.md`, spec §14, VLM spec §15 line, verify spec §10.2 line, README Benchmark pointer | 11 |

---

### Task 0: Constants — `rules.py` pins the spec's numbers; the test pins the constants

**Files:** create `bench/realeval/__init__.py` (empty), `bench/realeval/rules.py`, `tests/unit/test_realeval_rules.py`.

**Interfaces:**
```python
# bench/realeval/rules.py
SEED = 20261022
BOOTSTRAP_N = 10_000
ROUTE_MIN_PAGES = 60
ROUTE_MIN_DOCS = 5
ROUTE_MARGIN = 0.05            # spec §6.5: doubles to ROUTE_MARGIN_LOW_GT when the GT CER upper bound > GT_CER_SOFT
ROUTE_MARGIN_LOW_GT = 0.10
GT_CER_SOFT = 0.025
GT_SPOT_PAGES = 12             # 2 per zh document
ROUTE_DOC_WIN = 0.02
ROUTE_DOC_WINS_MIN = 5         # of 6
ROUTE_DOC_BIG_LOSS = 0.05
FORMULA_MATCH = 0.80
FORMULA_MIN_DOCS = 3           # of 4
FORMULA_MIN_DISPLAY = 15
FORMULA_RECOVERY_MIN = 0.50
FORMULA_GAP = 0.30
TEXT_GUARD = 0.05
VLM_RECALL_MIN = 0.95          # VLM spec §3.4 rule 1 — never changed here
VLM_MAX_SEC_PER_PAGE = 10.0
VLM_MAX_VRAM_MB = 6 * 1024
VLM_COVER_PHRASE = "wouldn't it be dreamy"
VLM_RENDER_DPI = 200
VLM_MAX_LONG_SIDE_PX = 1400
VLM_MAX_TOKENS = 4096
VLM_PINNED = {   # sha256 from docs/superpowers/spikes/2026-10-vlm-ocr.md
    "llamacpp_zip": "419de1292bcb772824fec45e4bc3722e79459e8d89c552578bafdad51266294a",
    "cudart_zip": "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668",
    "gguf": "f3ae46ec885050acf4b3d31944431e1fd90d50664fb09126af4a3c050ba14ee8",
    "mmproj": "204d757d7610d9b3faab10d506d69e5b244e32bf765e2bab2d0167e65e0a058a",
}
CONTROL_RENDER_DPI = 300
CONTROL_VALIDITY_NED = 0.85    # spec §7.0 check 3 on en-aashto-usrn-1984
SNIPPET_MAX = 30
```

- [ ] **Step 1: failing test** — `test_constants_match_spec` asserts each value above (write the literal numbers in the test, not `rules.X == rules.X`), and `test_vlm_thresholds_equal_rule_1` asserts `VLM_RECALL_MIN == 0.95`, `VLM_MAX_SEC_PER_PAGE == 10.0`, `VLM_MAX_VRAM_MB == 6144` with a docstring quoting VLM spec §3.4 rule 1.
- [ ] **Step 2:** create the module. Run `uv run pytest tests/unit/test_realeval_rules.py -q` → green.
- [ ] **Step 3: commit** `bench(realeval): pre-registered constants (spec 2026-10-22 §7)`.

---

### Task 1: Sample registry — manifest, sha256, gitignore guard, control rendering

**Files:** `bench/realeval/MANIFEST.json`, `bench/realeval/samples.py`, `tests/unit/test_realeval_samples.py`.

**MANIFEST.json** (one entry per document; values copied from `tests/fixtures/manual/realeval/INDEX.md` — no text):
```json
{"name": "zh-roc1912-gazette02", "group": "A", "role": "decision", "lang": "cht", "file": "original.pdf",
 "sha256": "4a1088c5…2565", "pdf_pages": 18, "gt_pages": [1,2,...,17], "gt_level": 3, "layout": "vertical",
 "spread": false, "ocr_text_layer": false}
```
Groups: `A` (6 zh scans), `B` (4 arXiv, `file: "paper.pdf"`, `gt: "gt/formulas.json"`, `html: "paper.html"`), `C` (2 en controls, `ocr_text_layer: true`, `lang: "en"`). `zh-supreme-court-1972` has `"spread": true` (informational; GT page N is the whole PDF page N). `zh-ncl-renjian-cihua.gt_pages` = `[1, 3, 9..41]`.

**Interfaces:**
```python
@dataclass
class RealSample:
    name: str; group: str; lang: str; pdf: Path; gt_dir: Path; gt_pages: list[int]; gt_level: int | str
    html: Path | None; sha256: str; ocr_text_layer: bool
    def gt_text(self, page: int) -> str          # reads gt/page-NNN.txt
    def scoring_pdf(self) -> Path                # original, or derived/image_only.pdf for group C

def guard_untracked(folder: Path) -> None        # `git check-ignore -q folder` must succeed and `git ls-files folder` be empty, else SystemExit
def load(root: Path, *, fake: bool = False) -> tuple[list[RealSample], list[dict]]   # (samples, missing); fake=True → SystemExit("real samples are never loaded in --fake mode")
def render_image_only(src: Path, dst: Path, dpi: int = CONTROL_RENDER_DPI) -> dict  # {"sha256", "pages", "text_chars"}; asserts text_chars == 0
```

- [ ] **Step 1: failing tests**
  - `test_manifest_has_twelve_documents_and_no_text`: 12 entries, groups 6/4/2, every entry has `sha256` of 64 hex chars, `gt_pages` lengths 9/35/17/22/4/2 for group A and 5/16 for group C, and no value longer than 80 characters (no document text can hide in there).
  - `test_load_rejects_sha_mismatch` (tmp folder with a copied `tests/fixtures/scanned_cht.pdf` and a manifest with a wrong hash → listed in `missing`, not in `samples`).
  - `test_load_refuses_tracked_folder` (monkeypatch `subprocess.run` so `git ls-files` returns one path → `SystemExit`).
  - `test_load_refuses_fake_mode` (`fake=True` → `SystemExit` before touching the filesystem — assert with a folder that does not exist).
  - `test_render_image_only_strips_text_layer`: render `tests/fixtures/text.pdf` → derived PDF has 3 pages, `pymupdf` extracts 0 characters, `aidoc.probe.probe_file(derived).text_ratio == 0`, page sizes equal the original's within 0.5 pt.
- [ ] **Step 2:** implement; `load()` resolves `gt_pages` and verifies that `gt/page-NNN.txt` exists for each listed page (missing GT file → the document goes to `missing` with a reason).
- [ ] **Step 3:** `uv run pytest tests/unit/test_realeval_samples.py -q` → green.
- [ ] **Step 4: commit** `bench(realeval): sample manifest, sha256 + untracked guard, image-only control rendering`.

---

### Task 2: Normalisation policy (spec §5.1) and errata

**Files:** `bench/realeval/normalize.py`, `bench/realeval/variants.json`, `tests/unit/test_realeval_normalize.py`.

**Interfaces:**
```python
TONE_MARKS = "ˇˊˋ˙"
APOSTROPHES = {"’": "'", "ʼ": "'"}
def norm_pred(markdown: str) -> str      # bench.metrics.plain → strip tone marks → apostrophes → pagemap.squash
def norm_gt(text: str) -> str            # strip tone marks → apostrophes → squash (errata are applied before, by apply_errata)
def apply_errata(gt_pages: dict[int, str], errata: list[dict], doc: str) -> dict[int, str]   # exact-substring replace, each erratum must match exactly once else ValueError
def variant_share(gt: str, pred: str) -> float | None   # diagnostic: share of substitutions covered by variants.json (+ opencc t2s if importable); None when no substitutions
```
`variants.json`: about 30 pairs of common glyph variants (`爲/為`, `衞/衛`, `甯/寧`, `裏/裡`, `羣/群`, `衆/眾`, `臺/台`, `麪/麵`, `爲/为`…) — characters only, no text.

- [ ] **Step 1: failing tests** (each a one-line assertion; keep them literal)
  - whitespace and line breaks vanish: `norm_gt("甲\n乙 丙") == "甲乙丙"`.
  - long s and ligature: `norm_gt("becauſe ﬁrſt") == "becausefirst"`; `norm_pred("because first") == "becausefirst"`.
  - full-width digits: `norm_gt("１９７３年") == "1973年"`.
  - punctuation and GT table pipes dropped: `norm_gt("一、任意搶擄者槍斃 | 甲") == "一任意搶擄者槍斃甲"`.
  - Bopomofo letters kept, tone marks dropped: `norm_gt("ㄌㄥˇ（冷）") == "ㄌㄥ冷"`.
  - simplified vs traditional is an error: `bench.metrics.ned(norm_gt("臺灣"), norm_pred("台灣")) < 1.0` and the Levenshtein distance is exactly 1.
  - variants are errors too: distance of `norm_gt("爲")` vs `norm_pred("為")` is 1; `variant_share("爲", "為") == 1.0`.
  - prediction markup removed: `norm_pred("# 標題\n\n<!-- page: 3 -->\n| a | b |\n|---|---|\n![](x.png) $x^2$ text") == "標題abtext"` (math goes to the formula metric, not here).
  - apostrophe: `norm_pred("Wouldn’t") == norm_gt("Wouldn't")`.
  - errata: the p12 entry replaces exactly once; an erratum whose `wrong` occurs twice or never raises `ValueError`.
  - identical function on both sides: `norm_gt(s) == norm_pred(s)` for a plain string without markup.
- [ ] **Step 2:** implement; `uv run pytest tests/unit/test_realeval_normalize.py -q` → green.
- [ ] **Step 3: commit** `bench(realeval): normalisation policy (spec §5.1), errata, variant diagnostic`.

---

### Task 3: Page-aligned scoring, paired bootstrap, document summaries

**Files:** `bench/realeval/scoring.py`, `tests/unit/test_realeval_scoring.py`.

**Interfaces:**
```python
def bag_prf(gt_sq: str, pred_sq: str) -> tuple[float, float, float]      # multiset recall, precision, F1 over characters
def page_rows(sample, gt_pages: dict[int, str], markdown: str | None) -> list[dict]
    # sections via aidoc.pagemap.split_pages(markdown); one row per GT page: {page, ned, cer, recall, precision, f1, dropped, gt_len, pred_len}
    # markdown None (failed) → every GT page scores 0 / recall 0 / dropped True
def doc_summary(rows: list[dict]) -> dict      # page-weighted means, worst page, n_pages, n_dropped
def paired_bootstrap(diffs: list[float], n: int = BOOTSTRAP_N, seed: int = SEED) -> tuple[float, float, float]   # (mean, lo, hi), percentile 2.5/97.5
def route_stats(per_doc: dict[str, dict[str, list[dict]]]) -> dict
    # per_doc[doc][config] = rows; returns {delta, lo, hi, n_pages, n_docs_both, wins_D, wins_M, big_loss_D, big_loss_M, per_doc: {doc: delta_doc}}
```

- [ ] **Step 1: failing tests**
  - `test_pages_without_gt_are_not_scored`: GT pages `[1, 3]`, markdown has pages 1–4 → 2 rows.
  - `test_missing_prediction_page_scores_zero`: GT pages `[1, 2]`, markdown has page 1 only → page 2 `ned == 0.0`, `dropped is True`.
  - `test_failed_conversion_scores_zero_everywhere`: `markdown=None` → all rows 0, `doc_summary()["ned"] == 0.0`.
  - `test_spread_page_maps_whole_section`: a 1-page "spread" GT containing right-half then left-half text vs a section with the same → `ned == 1.0`.
  - `test_bootstrap_is_deterministic_and_covers_mean`: `diffs = [0.1]*30 + [-0.05]*30` → `(mean, lo, hi)` with `lo < mean < hi`, identical across two calls, and `lo > 0` is False for `diffs = [0.01, -0.01]*30`.
  - `test_route_stats_counts_wins_and_big_losses`: build 6 docs where Docling beats MinerU by 0.03 on 5 and loses by 0.06 on 1 → `wins_D == 5`, `big_loss_D == 1`.
  - `test_bag_prf`: `bag_prf("甲乙乙", "乙甲") == (2/3, 1.0, 0.8)`.
- [ ] **Step 2:** implement using `bench.metrics.ned/cer` and `aidoc.pagemap.split_pages`; `uv run pytest tests/unit/test_realeval_scoring.py -q` → green.
- [ ] **Step 3: commit** `bench(realeval): page-aligned NED/CER/bag-of-chars, paired page bootstrap, route stats`.

---

### Task 4: Formula scoring against document-level GT, HTML text guard

**Files:** `bench/realeval/formulas.py`, `tests/unit/test_realeval_formulas.py`.

**Interfaces:**
```python
def norm_latex_doc(s: str) -> str              # bench.metrics.norm_latex + the spec §5.5 fixed list
def display_gt(formulas_json: Path) -> list[str]        # latex of items with display == "block"
def inline_gt(formulas_json: Path) -> list[str]
def pred_formulas(markdown: str) -> list[str]           # bench.metrics.formulas_in over the whole document
def recovery(gt: list[str], preds: list[str], thr: float = FORMULA_MATCH) -> dict   # {"R", "S", "n_gt", "n_pred", "matched"} greedy best-unused matching
def html_text(paper_html: Path) -> str                  # visible text with <math>, <script>, <style> removed, then squash
def ngram_recall(gt_sq: str, pred_sq: str, n: int = 5) -> float   # multiset recall of GT character 5-grams in pred
```

- [ ] **Step 1: failing tests**
  - `norm_latex_doc("3{+}1") == "3+1"`; `norm_latex_doc(r"\begin{aligned} a &= b \\ c &= d \end{aligned}") == "a=bc=d"`; `norm_latex_doc(r"\label{eq:1} x \tag{3} \nonumber") == "x"`; `norm_latex_doc(r"\mathbf{x}\text{ if }y") == "xify"`; `norm_latex_doc(r"\left( a \right)") == "(a)"`.
  - `display_gt` on a tmp `formulas.json` with 2 block + 3 inline items → 2.
  - `recovery(["a+b", "c^2"], ["c^{2}", "a + b", "zzz"])` → `R == 1.0`, `S == 1.0` (after normalisation), `matched == 2`; `recovery(["a+b"], [])` → `R == 0.0`, `S == 0.0`; a GT formula with best match NED 0.5 is not recovered at `thr=0.8` but counts in `S`.
  - `html_text` on a tmp HTML with `<p>Let <math alttext="x">…</math> be</p><script>no</script>` → `"letbe"`.
  - `ngram_recall("abcdefgh", "abcdefgh") == 1.0`; `ngram_recall("abcdefgh", "abcde") == 0.25`.
  - `pred_formulas` finds `$$…$$`, `$…$`, `\[…\]`, `\(…\)` across page markers (4 items from a 2-page sample).
- [ ] **Step 2:** implement; `uv run pytest tests/unit/test_realeval_formulas.py -q` → green.
- [ ] **Step 3: commit** `bench(realeval): document-level formula recovery (display only), LaTeX normalisation list, HTML text guard`.

---

### Task 5: Local VLM measurement over a loopback `llama-server`

**Files:** `bench/realeval/vlm_measure.py`, `tests/unit/test_realeval_vlm.py`.

**Interfaces:**
```python
def check_pinned(paths: dict[str, Path]) -> dict[str, bool]       # sha256 of llamacpp_zip / cudart_zip / gguf / mmproj vs rules.VLM_PINNED
def render_page_png(pdf: Path, page: int, dpi: int = VLM_RENDER_DPI, max_px: int = VLM_MAX_LONG_SIDE_PX) -> bytes
def ocr_request(base_url: str, png: bytes, *, timeout_s: int = 120) -> dict
    # POST {base_url}/chat/completions, messages=[{"role":"user","content":[{"type":"image_url","image_url":{"url": data-uri}}, {"type":"text","text":"OCR:"}]}],
    # max_tokens=VLM_MAX_TOKENS, temperature=0 → {"text", "seconds", "finish_reason", "usage"}; seconds = wall time of the request only
def parse_server_log(text: str) -> dict        # {"device_cuda": bool, "layers_on_gpu": int, "layers_total": int}
def page_recall(gt_sq: str, answer: str) -> float          # bag recall after norm_pred(answer)
def cover_hit(answer: str) -> bool                         # NFKC + apostrophes + lower, contains VLM_COVER_PHRASE
def measure(samples, cover_pdf: Path, base_url: str, *, out: Path, sampler_factory=Sampler) -> dict
    # writes vlm.jsonl; returns {per_doc: {name: {recall, pages, max_sec, mean_sec, length_pages}}, max_sec_overall, cover_hit, vram: {baseline, peak, delta}, load_seconds}
```
`measure()` calls `bench.run._check_vlm_endpoint(base_url)` first; the document recall is `sum(common) / sum(gt_len)` over the document's GT pages (spec §5.6).

- [ ] **Step 1: failing tests** (use `tests/fakes/vlm_server.py` `FakeVlmServer` with a scenario whose `pages` answers are known strings; see its module docstring for the scenario keys)
  - `test_non_loopback_is_refused`: `measure(..., base_url="http://10.0.0.5:8080/v1")` → `SystemExit` before any request.
  - `test_request_shape_is_the_spikes`: the fake's `calls.jsonl` shows one request per page, `max_tokens == 4096`, `temperature == 0`, the text part equals `"OCR:"`, the image is a data URI, and the PNG long side ≤ 1400.
  - `test_recall_and_timing_per_document`: two stand-in documents (1 and 2 pages, GT = the fake's answers) → recall 1.0 each, `max_sec_overall > 0`, `vlm.jsonl` has 3 rows with `finish_reason`.
  - `test_length_pages_are_counted_not_retried`: scenario rule `behavior: "length"` for page 2 → `length_pages == 1`, exactly one request for that page.
  - `test_cover_hit_normalises_apostrophe`: `cover_hit("…Wouldn’t It Be Dreamy…") is True`; `cover_hit("dreamy") is False`.
  - `test_parse_server_log`: a captured `-v` excerpt (`using device CUDA0 …`, 18 lines `layer N assigned to device CUDA0`, `n_layer = 18`) → `device_cuda True`, `18/18`.
  - `test_check_pinned`: temp files with known content → `False` for all four (content differs), and a monkeypatched sha → `True`.
  - `test_vram_sampler_is_used`: inject a fake `sampler_factory` returning fixed baseline/peak → `vram.delta == peak − baseline`.
- [ ] **Step 2:** implement (stdlib `urllib.request`, `base64`, PyMuPDF); `uv run pytest tests/unit/test_realeval_vlm.py -q` → green.
- [ ] **Step 3: commit** `bench(realeval): loopback PaddleOCR-VL measurement — spike S1 request shape, recall, timing, VRAM, cover phrase`.

---

### Task 6: `rules.decide()` — the §7 rules as a pure function

**Files:** `bench/realeval/rules.py` (append), `tests/unit/test_realeval_rules.py` (append).

**Interface:**
```python
def decide(route: dict | None, formulas: list[dict] | None, vlm: dict | None, gt_quality: dict, validity: dict) -> dict
    # route = scoring.route_stats(...) ; formulas = [{doc, R_M, R_D, T_M, T_D, n_display, math_hint, auto_reason, auto_engine, both_ok}]
    # vlm = vlm_measure.measure(...) output + {"pinned_ok": bool, "server_cuda": bool, "layers": "18/18", "nvidia_smi": bool}
    # gt_quality = {"cer_upper": float, "formula_gt_low_confidence": bool, ...}; validity = {"checks": {name: bool}}
    # → {"margin": 0.05|0.10, "validity_ok": bool,
    #    "zh_scan_route": {"verdict": "change"|"confirm"|"insufficient", "change_to": ["docling","mineru"]|None, "conditions": {name: {"value", "threshold", "ok"}}, "needed_samples": [...]},
    #    "formula_route": {"verdict": ..., "detector_note": str|None, "conditions": {...}},
    #    "vlm_local": {"verdict": "pass"|"fail"|"insufficient", "conditions": {...}, "on_pass": "plan: VLM local backend 8a (spec §7.3.3)"|None, "known_limitation": "scanned_cht repeated paragraph 0.75 (spec §7.3.4)"}}
```
Rules, verbatim from spec §7.1–§7.3: write them as small predicates (`_route_change`, `_route_confirm`, `_formula_side`, `_vlm_pass`) so each test can hit one branch. `validity_ok False` → every verdict `insufficient` with reason `"run invalid (validity checks)"`.

- [ ] **Step 1: failing tests** (construct inputs by hand; assert the verdict and the failing condition name)
  - zh route: `change` (Δ 0.08, lo 0.02, wins_D 5, big_loss_D 0, n 89, docs 6); `insufficient` for each single violation: Δ 0.04 (< margin), lo −0.01 (CI crosses 0), wins_D 4, big_loss_D 1, n_pages 59, n_docs_both 4; `confirm` for the mirror image; margin doubles when `cer_upper 0.03` so Δ 0.08 becomes `insufficient` with `conditions.delta.threshold == 0.10`.
  - formula route: `confirm` (3 of 4 papers R_M 0.7 / R_D 0.0 / T_M ≥ T_D − 0.05) with `detector_note None` when all `math_hint` True; same numbers with one `math_hint False` → `confirm` plus a `detector_note` mentioning new samples; `insufficient` when only 2 papers qualify, when `R_M 0.45`, when `T_M == T_D − 0.06`, when `formula_gt_low_confidence True`; `change` for the mirror image.
  - vlm: `pass` with 8 recalls ≥ 0.95, max_sec 4.7, delta 2400, cover True, pinned True; `fail` naming the condition for each of: one document 0.949, max_sec 10.1, delta 6145, cover False; `insufficient` for pinned False, `nvidia_smi False`, `server_cuda False`, 7 documents measured.
  - `validity_ok False` → three `insufficient`.
  - `test_decide_is_pure`: same inputs twice → equal JSON; inputs not mutated.
- [ ] **Step 2:** implement; `uv run pytest tests/unit/test_realeval_rules.py -q` → green.
- [ ] **Step 3: commit** `bench(realeval): decide() — the pre-registered rules of spec §7 as a pure function`.

---

### Task 7: Report — `summary.md` and the record skeleton, with no document text

**Files:** `bench/realeval/report.py`, `tests/unit/test_realeval_report.py`.

**Interfaces:**
```python
def summary(run: Path) -> str          # reads env.json, results.jsonl, pages.jsonl, vlm.jsonl, decisions.json, gt_quality.json → tables A/B/C/V + decisions + GT-confidence block + reruns + NONCLAIMS + the eval-specific non-claims (spec §10.2)
def record_skeleton(run: Path) -> str  # the committed record's fixed sections pre-filled from summary(); leaves "Deviations" and "Commentary" empty
def write_summary(run: Path) -> Path
```
Tables: A — per document × {auto, docling, mineru}: page-mean NED / CER / F1, worst page (number only); footer row Δ, CI, wins. B — per paper × config: R / S / T, `math_hint`, `auto` reason and engine. C — controls. V — per document recall, pages, max/mean s, `length` pages; cover hit; VRAM baseline/peak/delta; load seconds. Decisions: one block per decision listing every condition's value, threshold and ok.

- [ ] **Step 1: failing tests**
  - `test_summary_has_fixed_sections`: a tmp run written by hand (two docs, one paper, vlm rows) → all section headings present in order; the NONCLAIMS text appears once per table group.
  - `test_summary_contains_no_document_text`: put a 40-character unique sentence into the tmp GT and into a tmp `out-docling/<doc>/<doc>.md`; the summary and the record skeleton must not contain any 31+-character substring of it (sliding window check).
  - `test_decisions_rendered_with_conditions`: each condition name, value and threshold from `decisions.json` appears.
  - `test_gt_confidence_block`: level, spot pages, k, CER, upper bound, margin value present.
- [ ] **Step 2:** implement; `uv run pytest tests/unit/test_realeval_report.py -q` → green.
- [ ] **Step 3: commit** `bench(realeval): summary and record skeleton; numbers only`.

---

### Task 8: Orchestration (`run.py`), GT-quality tool, fake dry run

**Files:** `bench/realeval/run.py`, `bench/realeval/gt_quality.py`, `tests/unit/test_realeval_run.py`, `tests/unit/test_realeval_gt_quality.py`.

**CLI:**
```
uv run python bench/realeval/run.py --part scans|formulas|vlm|all --out bench/results/<date>-realeval
       [--root tests/fixtures/manual/realeval] [--resume] [--decide-only]
       [--fake --stand-in <dir>]                 # dry run: synthetic stand-ins + AIDOC_FAKE_ENGINES + tests/fakes/vlm_server
       [--vlm-base-url http://127.0.0.1:<port>/v1 --vlm-artefacts <dir>]   # llama-server started by hand (protocol below)
uv run python bench/realeval/gt_quality.py --draw --root <realeval>          # prints the 12 seeded spot-check pages (2 per zh document)
uv run python bench/realeval/gt_quality.py --summarise --root <realeval>     # gt_spotcheck.json + gt_errata.json + alignment.json → gt_quality.json (CER, Wilson upper, margin)
```

**run.py behaviour:**
1. `samples.load(root, fake=…)`; `guard_untracked`; group C → `render_image_only` into `<root>/<doc>/derived/image_only.pdf`; record all sha256 and `text_chars` in `env.json`.
2. `env.json` = `bench.run.environment(...)` fields + spec §3 additions (`spec_commit`, `gt_quality_sha256`, `samples: {name: sha256}`, `derived: {...}`, `vlm_pinned: {...}`, `offline_declared: bool` from `--offline-declared`), `reruns: []`.
3. Validity check 2 (`aidoc_dirty == false`) before converting; refuse otherwise unless `--fake`.
4. Scans + controls: for config in `auto, docling, mineru` × documents: `bench.run.convert(Sample(name, "R", pdf, None, "realeval"), config, out, fake=…)` with `--lang` from the manifest (extend the command through `env`/argument: `convert()` takes no lang — add an optional `extra_args` parameter to `bench.run.convert`, defaulting to `()`, and a one-line test in `tests/unit/test_bench_run.py` if it exists, else in `test_realeval_run.py`); score with `scoring.page_rows`; append `pages.jsonl` and `results.jsonl`; `--resume` skips pairs already present.
5. Formulas: same three configs on `paper.pdf`; `formulas.recovery`, `ngram_recall`; `probe_file(pdf).math_hint`; `auto` reason from the sidecar (`route.reason` if present in the payload, else from `tried`).
6. VLM: `vlm_measure.check_pinned` → `measure()`; `vlm.jsonl`.
7. Validity checks (spec §7.0) → `validity.json`; `rules.decide(...)` → `decisions.json`; `report.write_summary`.
8. `--decide-only`: recompute `decisions.json` from the files on disk and print a diff against the existing one (verifier's check 6).
9. Every conversion failure caused by the harness (timeout, I/O) is retried once **only** when `--rerun-pair config/doc --reason "..."` is passed; the rerun is appended to `env.json.reruns`.

**gt_quality.py:** `draw(manifest, seed=SEED)` → `{doc: [p1, p2]}` via `random.Random(SEED).sample(gt_pages, 2)` per zh document in manifest order; `summarise()` reads `gt_spotcheck.json` (`[{doc, page, k, n}]`), `gt_errata.json`, `alignment.json` (`[{doc, first_ok, last_ok, spread_order_ok?}]`), formula spot-check (`[{doc, index, ok}]`) → `gt_quality.json` with `cer`, `cer_upper` (Wilson 95 % upper), `margin` (0.05 or 0.10), `formula_gt_low_confidence` (≥ 3 of 12 not ok), `errata_count`, `alignment_ok`, `author`, `date`.

- [ ] **Step 1: failing tests**
  - `test_draw_is_seeded_and_stratified`: a manifest with 6 zh docs → 12 pages, 2 per doc, same on two calls, every page in that doc's `gt_pages`.
  - `test_wilson_upper_and_margin`: `k=0, n=2000` → `cer_upper < 0.002`, margin 0.05; `k=60, n=2000` → `cer_upper > 0.025`, margin 0.10.
  - `test_formula_gt_low_confidence`: 3 of 12 not ok → True; 2 → False.
  - `test_fake_dry_run_writes_everything` (the plumbing test, no GPU): build a stand-in folder in `tmp_path` shaped like the real one — `zh-*` from `tests/fixtures/scanned_cht.pdf` with GT pages written from `make_fixtures` strings, one `arxiv-*` from `tests/fixtures/formula.pdf` with a tiny `formulas.json` and `paper.html`, one `en-*` from `tests/fixtures/text.pdf` (has a text layer → exercises rendering) — plus a manifest with the stand-ins' real sha256; run `run.run(["--part", "all", "--fake", "--stand-in", str(tmp), "--out", str(out), "--vlm-base-url", fake_server.url])` → `env.json`, `pages.jsonl`, `results.jsonl`, `vlm.jsonl`, `validity.json`, `decisions.json`, `summary.md` exist; all three verdicts are `insufficient` (tiny n) with `n_pages` reported; no file under the real `tests/fixtures/manual/realeval` was read (monkeypatch `samples.load` to assert `root == tmp`).
  - `test_fake_mode_never_loads_real_samples`: `--fake` without `--stand-in` → `SystemExit`.
  - `test_decide_only_matches`: after the dry run, `--decide-only` exits 0 and reports no diff.
  - `test_dirty_tree_is_refused_for_real_runs`: monkeypatch `environment()` to return `aidoc_dirty True` with `--fake` absent → `SystemExit` before `convert`.
- [ ] **Step 2:** implement; `uv run pytest tests/unit/test_realeval_run.py tests/unit/test_realeval_gt_quality.py -q` → green; then the whole suite `uv run pytest -q` → green (bench tests included).
- [ ] **Step 3: commit** `bench(realeval): run.py orchestration (reuses bench.run), gt_quality tool, fake dry run`.

---

### Task 9: Ground-truth preparation — by the independent verifier, before the freeze (spec §6)

No code. Done by the verifier, not the implementer, on the dev host; nothing here is committed (the folder is gitignored) except the sha256 of the result, which goes into `env.json` at run time.

- [ ] **Step 1: alignment** (spec §6.2): for each of the 8 scan documents open the PDF and compare page 1 and the last GT page with `gt/page-NNN.txt`; for `zh-supreme-court-1972` confirm "right page first, then left" matches the GT order. Write `alignment.json`. A mismatch is fixed in `MANIFEST.json` (page mapping) and committed before the freeze.
- [ ] **Step 2: errata** (spec §6.1): `gt_errata.json` starts with the p12 Bopomofo entry from `zh-ssid-zhuyin-primer/SOURCE.md`; add any error found during Step 3 (image vs GT, never engine output vs GT).
- [ ] **Step 3: text spot-check** (spec §6.3): `gt_quality.py --draw` → 12 pages; judge each against the scan image after applying errata; count substitutions / omissions / insertions over the page's GT (punctuation, whitespace ignored; glyph variants as printed are correct). Write `gt_spotcheck.json` with `k`, `n` per page.
- [ ] **Step 4: formula spot-check** (spec §6.4): 3 display formulas per paper by `random.Random(SEED)` over the block indices (the tool prints them); compare with the rendered PDF; write `formula_spotcheck.json`.
- [ ] **Step 5:** `gt_quality.py --summarise` → `gt_quality.json`; note `cer`, `cer_upper`, `margin`, `formula_gt_low_confidence`; compute its sha256 and keep it for the record.

---

### Task 10: Freeze and the run-once protocol (spec §8)

- [ ] **Step 1: freeze.** All of Tasks 0–8 merged on `master`; the spec is in its final form; `uv run pytest -q` green; `git status --porcelain --untracked-files=no` empty. Note the commit hash as `FREEZE`.
- [ ] **Step 2: artefacts.** Verify the four pinned files (`check_pinned`) under a local folder (`envs/vlm/` is gitignored; or any path passed as `--vlm-artefacts`). Any mismatch → stop; the VLM part will be `insufficient` and the reason is recorded.
- [ ] **Step 3: environment.** Close other GPU applications where possible; record `nvidia-smi` baseline; optionally disconnect the network (`--offline-declared`). Engines `docling` and `mineru` `.ready`.
- [ ] **Step 4: scans + controls + formulas** (single command, resumable):
  `uv run python bench/realeval/run.py --part scans --out bench/results/<date>-realeval` then `--part formulas --resume`.
- [ ] **Step 5: VLM.** Start `llama-server -m <gguf> --mmproj <mmproj> --host 127.0.0.1 --port <free> -ngl 99 -c 8192 --temp 0 -np 1 --no-ui -v > <out>/llama-server.log 2>&1`; wait for `/health` 200 (record load seconds); `run.py --part vlm --resume --vlm-base-url http://127.0.0.1:<port>/v1 --vlm-artefacts <dir>`; stop the server; confirm no `llama-server` process remains.
- [ ] **Step 6: decide + report.** `run.py` wrote `validity.json`, `decisions.json`, `summary.md`. Read `validity.json` first: if a check failed, fix the harness cause, note it in `env.json.reruns` with the reason, and rerun that part once (spec §8.3). **A result that disagrees with expectations is not a reason to rerun.**
- [ ] **Step 7:** nothing is changed after this point except documentation (Task 11).

---

### Task 11: Record and cross-references

**Files:** create `docs/superpowers/evals/2026-10-22-real-sample-eval-record.md`; modify spec §14; one line each in `docs/superpowers/specs/2026-10-05-vlm-ocr-engine-design.md` §15 and `docs/superpowers/specs/2026-10-05-verify-and-benchmark-design.md` §10.2 (after the "路由問題" paragraph); README "Benchmark" section (one line: link + the three verdicts).

- [ ] **Step 1:** `report.record_skeleton(run)` → the record file; fill "Deviations" (anything the run did differently from this plan, with the reason) and "Commentary" (interpretation only — the verdicts come from `decisions.json` and are quoted, not restated).
- [ ] **Step 2:** spec §14: date, `FREEZE` commit, `env.json.aidoc_commit`, the three verdicts, the record link, and the follow-up plan file names (or "none"). If `vlm_local == pass`, the follow-up plan is named `docs/superpowers/plans/<date>-vlm-local-backend-8a.md` and its scope is spec §7.3.3 verbatim (Task 4b + 8a, `scanned_cht` as strict xfail, README/Settings known limitation, VLM spec §13 #6 amendment) — **not written or started in this plan**. If `zh_scan_route == change`, the follow-up plan is `docs/superpowers/plans/<date>-zh-scan-route.md` (spec §7.1 scope). If `formula_route` has a detector note, the follow-up is a sample-collection task (spec §11), not code.
- [ ] **Step 3:** cross-reference lines and README pointer; `uv run pytest -q` still green (docs only).
- [ ] **Step 4: commit** `docs(realeval): run record, decisions, cross-references` — the commit touches only `docs/` and `README.md`.

---

## Run-once protocol (one page, for the person at the keyboard)

1. Verifier finished Task 9; `gt_quality.json` exists; its sha256 noted.
2. `FREEZE` commit exists; tree clean; `uv run pytest -q` green.
3. Pinned artefacts verified; GPU baseline recorded; other GPU apps closed; network optional (declare).
4. `run.py --part scans`, `--part formulas --resume`, start `llama-server`, `--part vlm --resume`, stop server.
5. Read `validity.json`. Pass → go to 6. Fail → fix harness cause, record in `env.json.reruns`, rerun that part once.
6. Do not look at `decisions.json` before `validity.json`. Then read it; it is the verdict.
7. Task 11 only touches documentation.
8. The 12 documents are now a regression set, never again decision evidence (spec §8.2).

## Acceptance checklist (independent verifier; mirrors spec §13)

| # | Check | How |
|---|---|---|
| 1 | No code impact | `git diff --stat FREEZE..HEAD -- src/ web/` empty; `uv run pytest -q` green at HEAD |
| 2 | Freeze | `git log -1 --format=%cI FREEZE` earlier than `env.json.date`; `env.json.aidoc_commit` is `FREEZE` or a docs-only descendant; `aidoc_dirty == false`; `uv run pytest tests/unit/test_realeval_rules.py -q` green (constants = spec §7) |
| 3 | Samples | `git ls-files tests/fixtures/manual` prints nothing; `env.json.samples` sha256s equal `INDEX.md`; `env.json.derived.*.text_chars == 0` for both controls |
| 4 | GT quality | `sha256sum tests/fixtures/manual/realeval/gt_quality.json` equals `env.json.gt_quality_sha256`; its `date` precedes `env.json.date`; `decisions.json.margin` equals its `margin` |
| 5 | Privacy | `env.json.vlm.endpoint_host` is loopback; `grep backend bench/results/<run>/aidoc-*.toml` shows `"none"` three times; `uv run pytest tests/unit/test_realeval_report.py -q` green and a manual skim of the record for text |
| 6 | Mechanical decision | `uv run python bench/realeval/run.py --decide-only --out bench/results/<run>` prints "no diff"; the record quotes the same verdicts and condition values |
| 7 | Reruns | `env.json.reruns` empty, or each entry names a §7.0 validity check or a harness failure with the pair |
| 8 | Thresholds unchanged | `grep -n "0.95\|10.0\|6 \* 1024" bench/realeval/rules.py` matches VLM spec §3.4 rule 1; `grep scanned_cht bench/results/<run>/vlm.jsonl` finds nothing; the record's known-limitation line cites spec §7.3.4 |
| 9 | Record complete | every fixed section of spec §10.2 present; each verdict is one of the allowed words; each condition shows value, threshold, ok |
| 10 | Follow-ups only planned | any `change` / `pass` maps to a plan file name in spec §14 that does not exist yet, or to a sample-collection item of spec §11 |

## Coverage table (spec section → task)

| Spec | Task |
|---|---|
| §2 samples, §4.1–4.3 preprocessing | 1 |
| §4.4, §5.1 normalisation, §5.4 variant diagnostic | 2 |
| §5.2–5.3 page metrics, bootstrap, route stats | 3 |
| §5.5 formulas, text guard | 4 |
| §5.6 VLM measurement | 5 |
| §7 rules, §6.5 margin | 0, 6, 8 (gt_quality) |
| §6.1–6.4 GT preparation | 9 |
| §8 freeze, once-only, reruns | 8 (guards), 10 |
| §9 privacy | 1 (guard), 5 (loopback), 7 (no text), 8 (`backend = "none"`) |
| §10 recording | 7, 11 |
| §13 acceptance | checklist above |

## Implementation notes / deviations

(Filled by the implementer during Tasks 0–8 and by the person running Task 10: every ruling that differs from the plan, with the test that pins it.)

**Tasks 0–8 (implementer, 2026-10-22).** No real sample was converted or scored. Only metadata was read: `INDEX.md`, the `gt/` file *names* (to confirm the page lists, Task 1), and the JSON *shape* of the four `formulas.json` (a list of `{display, latex}`; the block counts 68/18/191/110 match the manifest; no formula text printed, Task 4).

| # | Task | Deviation from the plan | Why | Pinned by |
|---|---|---|---|---|
| 1 | 1 | `samples.load()` gains `manifest=` and `stand_in=`. `fake=True` without `stand_in` still refuses before touching the disk. Stand-in mode needs its own manifest, refuses the real folder and skips the git guard. | The plan's `load(fake=True) → SystemExit` left the `--fake` dry run no way to load stand-ins. | `test_realeval_samples.py::test_load_refuses_fake_mode`, `::test_stand_in_mode_refuses_the_real_folder` |
| 2 | 3, 6 | `rules.EPS = 1e-9` tie tolerance at every threshold. Route aggregates (Δ, CI, per-doc Δ) are not rounded; page rows keep 4 decimals as `bench.metrics` does. | `0.83 − 0.80` is `0.0299…` and `0.7 − 0.4` is `0.2999…`: without a tolerance, a value exactly at a spec threshold would be decided the wrong way, and rounding could move a value across a threshold. | `test_realeval_rules.py::test_zh_route_exact_threshold_counts`, `::test_formula_route_gap_exactly_at_threshold_counts`, constants test |
| 3 | 3 | Page rows carry `failed`; `route_stats` counts `n_docs_both` from it, and failed documents still enter Δ at NED 0. | Spec §7.0: at least 5 documents must be converted by both engines. | `test_realeval_scoring.py::test_route_stats_failed_conversion_counts_as_zero_but_not_as_both_done` |
| 4 | 4 | `norm_latex_doc` strips the `\left`/`\right` **commands** (and a null `.` delimiter) before calling `bench.metrics.norm_latex` unchanged. | Called alone, `norm_latex` also drops the delimiter (`\left( a \right)` → `a`). Spec §5.5 says "去 `\left`／`\right`" and the plan's test expects `(a)`. | `test_realeval_formulas.py::test_norm_latex_doc` |
| 5 | 4 | `\\` is removed before `norm_latex` runs, and "single-character braces" means any one non-backslash character (`{+}`, `{=}`, `c^{2}`), applied until stable. Multi-character braces stay. | `norm_latex`'s spacing rule `\\[,;:! ]` would eat `\ ` out of `\\ `. The plan's `c^{2}` vs `c^2` → S = 1.0 test needs the general reading. | same |
| 6 | 5 | `measure()` launches `llama-server` itself (`LlamaServer`, the spike's arguments plus `-v`) **inside** the VRAM sampler, and times model load to `/health` 200. A real run takes `--vlm-artefacts` only; `--vlm-base-url` is for `--fake`. | Spec §5.6 defines delta as peak minus the baseline **before the server starts**. With the server started by hand (plan Task 10 step 5), the baseline would already include the loaded model and VRAM would be under-reported. **Task 10 step 5 is therefore:** `run.py --part vlm --resume --out <run> --vlm-artefacts envs/vlm --vlm-artefacts data/models/vlm`. The harness starts and stops the server, and its log goes to `<run>/llama-server.log`. | `test_realeval_vlm.py::test_launched_server_baseline_first_then_load_seconds`, `test_realeval_run.py::test_real_vlm_part_launches_the_pinned_server` |
| 7 | 5 | `tests/fakes/vlm_server.py` also records `temperature` and `n_images`, and answers `GET /health`. The fake keys pages by a "Page N of M" prompt, which the spike prompt `"OCR:"` does not carry, so the tests drive per-request behaviour with `request_no` rules. | The request-shape test must see `temperature == 0`. | `test_realeval_vlm.py::test_request_shape_is_the_spikes` |
| 8 | 5, 8 | The pinned check also requires the `llama-server.exe` that runs to be byte-identical to the member in the pinned b11559 zip. Any mismatch means the VLM is not run and (2) is `insufficient`. | "Reproducible install" (spec §7.3.1) is about the binary that actually runs. | `run.exe_from_zip` |
| 9 | 6 | New constants `VLM_DOCS = 8` and `VLM_LAYERS = 18` (spec §7.0 / §5.6 numbers). `vram_measured` also needs a pre-server baseline. `detector_note` is set only on `confirm`; `detector_finding` carries the positive "rule holds" sentence. | The spec gives the numbers; §7.2 ties the detector sub-conclusion to `confirm`. | `test_realeval_rules.py` |
| 10 | 7 | NONCLAIMS appears after each table group (A, B, C, V) and again in the closing 不宣稱 section: 5 times. The VLM summary row of `vlm.jsonl` carries `per_doc`. | bench/report.py pattern + spec §10.2. | `test_realeval_report.py::test_summary_has_fixed_sections` |
| 11 | 8 | Inputs are staged as `<out>/inputs/<doc>.pdf` (hardlink, else copy; same sha256). | `aidoc` names output folders after the file stem, and every A/C original is `original.pdf`, so outputs would overwrite each other. | `test_realeval_run.py::test_dry_run_scores_the_derived_control` |
| 12 | 8 | The `auto` route reason is `aidoc.router._rule(probe_file(pdf), opts)[1]`, recomputed by the harness. `tried` from the payload is recorded beside it. | The `aidoc convert --json` payload carries no route reason, and `src/` may not change. | `test_realeval_run.py::test_route_reason_is_the_routers_rule` |
| 13 | 8 | A real run refuses to start, before touching any sample, when the tree is dirty or when `gt_quality.json` or `gt_errata.json` is missing. `gt_quality.json` is copied into the run folder, where `--decide-only` reads it. `--resume` refuses a different `aidoc_commit` or `gt_quality.json`. | Spec §6.5 and §8.1: the margin and errata are fixed before the first conversion. | `test_realeval_run.py::test_dirty_tree_is_refused_for_real_runs`, `::test_real_run_needs_gt_quality_before_any_conversion` |
| 14 | 9 | `gt_quality.py --draw` uses one `random.Random(SEED)` across the six zh documents in manifest order, and a second `random.Random(SEED)` for 3 display-formula indices per paper (0-based in the paper's `block` list), taken from the manifest's `n_display`. `--summarise` refuses any page or index that was not drawn, and needs `--author`. | Spec §6.3 / §6.4: pages are drawn, not chosen. | `test_realeval_gt_quality.py` |

**Open spec question (before the freeze):** spec §7.0 says any validity failure invalidates the whole run, and its check 5 is the VLM setup (CUDA0, 18/18 layers, loopback, sha256). Spec §7.3.2 lists the same failures as an `insufficient` VLM verdict. The harness applies §7.0 literally, so a failure of check 5 also turns both route verdicts `insufficient`. If the intent is that a VLM setup failure affects decision (2) only, amend the spec (and `run.validity` / `rules.decide`) before Task 10.
