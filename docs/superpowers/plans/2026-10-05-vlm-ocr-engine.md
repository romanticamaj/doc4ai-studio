# VLM OCR Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Read `docs/superpowers/plans/2026-09-24-index.md` (cross-phase contracts) and the spec first; Task 0 amends the index — every later task relies on the amended contracts. Task 1 is a spike with a decision rule: its outcome selects which of Tasks 8a/8b/8c is executed.

**Goal:** A fourth engine `vlm` — one engine name, two backends behind one HTTP runner: `local` (PaddleOCR-VL-1.6 GGUF served by a `llama-server` child process the runner manages) and `openai` (any OpenAI-compatible vision endpoint, off by default, explicit opt-in). It is the last fallback of every PDF/image route and the last engine of the per-page repair order. With `backend = "none"` nothing changes for existing users.

**Spec:** `docs/superpowers/specs/2026-10-05-vlm-ocr-engine-design.md` (Traditional Chinese; §3 holds the source verification and the spike decision rule; §4–§11 the design). Related: `2026-10-01-per-page-quality-and-page-map.md` (repair model), index `2026-09-24-index.md`.

**Architecture:** `engines/vlm.py` (`VlmEngine(RunnerEngine)`) runs `engines/runner/vlm_runner.py` on the host interpreter (stdlib + PyMuPDF, like the fake runner). The runner renders pages to PNG, sends one Chat Completions request per page to `base_url` (remote) or to a `llama-server` it spawned on loopback (local), cleans the Markdown, emits `<!-- page: N -->` per page (`page_map_method = "vlm_per_page"`), reports refusals/truncations as `failed_pages`, and returns `vlm` usage in `result.json`. `router.py` appends `vlm` to every PDF/image order; `repair.py` appends it to the repair order; `pipeline.py` writes usage to `vlm_usage` (schema v6 — v5 went to the compat API's `compat_tasks`) and enforces the daily budget / per-document cap; `vlm_runtime.py` installs the pinned llama.cpp build and GGUF. Tests use `tests/fakes/vlm_server.py` (a scripted OpenAI-compatible server) and `tests/fakes/fake_llama_server.py`.

**Tech Stack:** Python 3.10 host (PyMuPDF, FastAPI, stdlib `http.server`/`urllib`), llama.cpp prebuilt (Windows `cuda-13` build, tag ≥ b8110) + `PaddlePaddle/PaddleOCR-VL-1.6-GGUF`, React/TypeScript web, Vitest, Playwright.

## Global Constraints

Index §0 plus:
- The main program never installs paddle, torch, or an HTTP client library for this feature: `vlm_runner.py` uses `urllib.request` + `http.client`; `vlm_runtime.py` downloads with `urllib`.
- `vlm_runner.py` imports only stdlib + `pymupdf` + the sibling `_proto.py` / `_vlm_prompt.py` (it runs on the host interpreter, same rule as `fake_runner.py`).
- API keys never appear in `request.json`, `result.json`, logs, sidecars, SSE payloads or test fixtures. Tests grep for the fake key.
- `backend = "none"` (default) must leave `route()` output, repair order, sidecar shape and every existing test unchanged.
- VLM is always the **last** engine of a route (after MarkItDown for `pdf_default`) and the **last** repair engine (index A21).
- Pinned downloads (llama.cpp tag, GGUF files) live in one table in `vlm_runtime.py` with sha256; changing them is a code change with a test.
- Prompt text is pinned by `prompt_version` + sha256 test (`_vlm_prompt.py`); any prompt change bumps the version.
- Thresholds/constants: `REMOTE_RETRY_BACKOFF = (2, 8, 30, 60)`, `REMOTE_MAX_RETRIES = 4`, `RETRY_AFTER_CAP_S = 120`, `TRUNCATION_RETRY_FACTOR = 2`, `MAX_OUTPUT_TOKENS_CAP = 16384`, `BAD_REQUEST_DPI_STEP = 50`, `MIN_DPI = 72`, `VLM_OVERLAP_MIN = 0.2` (advisory only).
- Commit trailer for every commit (both lines):
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0175gLq9uam6M5Z9yNNxM4Ba
  ```

## Review Focus

1. Zero impact with `backend = "none"`: the parametrised router test runs the existing cases against `{"vlm": False}` and asserts byte-identical `RouteDecision`s (Task 5).
2. No request leaves the machine unless `remote_enabled` is true: the fake endpoint's call log must be empty in the "disabled" reliability scenario (Task 12).
3. The key is never written: Task 4's test greps `request.json`, the runner log tail and the sidecar for the fake key.
4. Refused or truncated pages are never accepted as repairs and never silently kept as good pages (Task 4 + Task 6).
5. `options_key()` includes backend + model so a model change never reuses old VLM segments (Task 7).
6. `llama-server` never outlives the runner: Task 4b's fake server test asserts the child is gone after `close()` and after `kill()`.
7. Spike decision is recorded with numbers before Task 8 starts; the plan forbids implementing a local path the spike rejected.

---

## Index amendments (applied by Task 0 to `docs/superpowers/plans/2026-09-24-index.md`)

- **§0 new bullets**
  - `vlm` engine: `vlm_runner.py` runs on the host interpreter (stdlib + pymupdf); the local runtime is a pinned llama.cpp prebuilt under `data/tools/llamacpp/<tag>/` plus GGUF files under `data/models/vlm/`; `envs/vlm/` holds only `manifest.json` and `.ready` (no `.venv` unless spike S1 selects the transformers path).
  - VLM is the last engine of every PDF/image route and the last repair engine; `backend = "none"` is the default and changes nothing.
- **§2 new rows**
  - A21 | Where does the VLM sit? | Always last in routing (after MarkItDown for `pdf_default`) and last in repair. Never before a cheaper engine. Spec 2026-10-05 VLM §5–§6.
  - A22 | Remote VLM privacy. | `remote_enabled` defaults to false; loopback `base_url` is "local" (info, not warning) but still needs the switch; every remote use logs `vlm(openai): sending N page image(s) to <host>`; sidecar records `vlm.endpoint_host` and `pages_sent`; keys only via env (`api_key_env`) or masked config.
  - A23 | Segment reuse across VLM models. | `options_key()` includes `vlm_backend` and `vlm_model`; `vlm_remote` is not part of it.
- **§1 layout**: `src/aidoc/engines/vlm.py`, `src/aidoc/engines/runner/vlm_runner.py`, `src/aidoc/engines/runner/_vlm_prompt.py`, `src/aidoc/engines/runner/selfcheck_vlm.py`, `src/aidoc/vlm_runtime.py`, `envs/vlm/manifest.json` (+ `.ready`), `tests/fakes/vlm_server.py`, `tests/fakes/fake_llama_server.py`, `docs/superpowers/spikes/2026-10-vlm-ocr.md`.
- **§3 models** (additive): `ENGINE_NAMES = ("markitdown", "docling", "mineru", "vlm")`; `ConvertOptions.vlm_remote: bool | None = None`; `RawResult.extra: dict = field(default_factory=dict)` (runner `result.json` extras such as `vlm`); `QualityResult.pages[*].metrics` may carry `vlm_overlap`.
- **§4 Store**: schema v6 (renumbered: v5 is the compat API's `compat_tasks`) adds `vlm_usage(day, backend, endpoint_host, pages, requests, prompt_tokens, completion_tokens, PRIMARY KEY (day, backend, endpoint_host))`; `Store.add_vlm_usage(...)`, `Store.vlm_usage_today(backend, host)`, `Store.vlm_usage_days(n)`, `Store.prune_vlm_usage(keep_days=180)`.
- **§5 runner protocol**: `engine_opts` for `vlm` (spec §4.2 two shapes); `result.json` may carry `vlm: {backend, model, endpoint_host, pages_sent, usage, truncated_pages, refused_pages, dpi, prompt_version}`; `page_map_method` enum gains `vlm_per_page`; runner env may carry `AIDOC_VLM_API_KEY_INLINE` (host-injected, never in request.json).
- **§6 fake scenario**: `engine` may be `vlm`; new behaviours `refuse`, `length` (fake runner side) mirror the fake endpoint's; `FakeEngine("vlm").supports()` = pdf/image.
- **§7 config**: `[vlm]` section exactly as spec §8.3.
- **§8 API**: `GET /system` → `engines.vlm` extra fields (spec §10.1); `POST /system/vlm/check` → `202` + SSE `setup.*` with `engine: "vlm"`; `GET /system/vlm/usage?days=30` → `{days: [{day, backend, endpoint_host, pages, requests, prompt_tokens, completion_tokens}], today: {...}}`; `PUT /settings` `[vlm]` validation + `api_key` masking.

## File map

| Path | Change |
|---|---|
| `docs/superpowers/plans/2026-09-24-index.md`, `src/aidoc/models.py`, `src/aidoc/config.py`, `src/aidoc/server/api/settings.py` | Task 0 |
| `docs/superpowers/spikes/2026-10-vlm-ocr.md`, `.superpowers/spike/vlm_s1_*.py` (not committed) | Task 1 (spike S1), Task 2 (spike S2, optional) |
| `tests/fakes/vlm_server.py`, `tests/unit/test_fake_vlm_server.py` | Task 3 |
| `src/aidoc/engines/runner/_vlm_prompt.py`, `src/aidoc/engines/runner/vlm_runner.py`, `tests/unit/test_vlm_runner.py` | Task 4 |
| `tests/fakes/fake_llama_server.py`, `vlm_runner.py` (local server lifecycle), `src/aidoc/procs.py`, `tests/unit/test_vlm_local_server.py` | Task 4b |
| `src/aidoc/engines/vlm.py`, `src/aidoc/engines/registry.py`, `src/aidoc/engines/fake.py`, `src/aidoc/engines/runner/fake_runner.py`, `src/aidoc/router.py`, `tests/unit/test_router.py`, `tests/unit/test_vlm_engine.py` | Task 5 |
| `src/aidoc/repair.py`, `tests/unit/test_repair.py` | Task 6 |
| `src/aidoc/store.py`, `src/aidoc/pipeline.py`, `src/aidoc/output.py`, `src/aidoc/engines/base.py`, `src/aidoc/server/maintenance.py`, `tests/unit/test_store.py`, `tests/unit/test_pipeline_vlm.py` | Task 7 |
| `src/aidoc/vlm_runtime.py`, `src/aidoc/setup_engines.py`, `src/aidoc/engines/runner/selfcheck_vlm.py`, `tests/unit/test_vlm_runtime.py` | Task 8a (spike path b) / 8b (path c) / 8c (remote only) |
| `src/aidoc/quality.py`, `tests/unit/test_quality.py` | Task 9 |
| `src/aidoc/server/api/system.py`, `src/aidoc/server/api/settings.py`, `src/aidoc/server/setup_runner.py`, `tests/api/test_system_vlm.py`, `tests/api/test_settings_vlm.py` | Task 10 |
| `web/src/api/types.ts`, `web/src/features/settings/{EngineCard,VlmBackendForm}.tsx`, `web/src/features/settings/isLoopbackUrl.ts`, `web/src/pages/SettingsPage.tsx`, `web/src/pages/ConvertPage.tsx`, Vitest files | Task 11 |
| `web/src/features/document/pageWarnings.ts`, `web/src/features/jobs/buildTimeline.ts`, `web/src/features/library/*`, `src/aidoc/mcp/schemas.py`, `web/e2e/vlm.spec.ts` | Task 11b |
| `tests/reliability/test_vlm.py` | Task 12 |
| `tests/integration/test_engine_vlm.py`, `tests/integration/test_vlm_repair_real.py`, `README.md`, `skill/SKILL.md`, spec acceptance record, this file's deviations | Task 13 |

---

### Task 0: Contracts — index amendments, models, config, settings whitelist

**Files:**
- Modify: `docs/superpowers/plans/2026-09-24-index.md`, `src/aidoc/models.py`, `src/aidoc/config.py`, `src/aidoc/server/api/settings.py`, `tests/unit/test_models.py`, `tests/unit/test_config.py`

**Interfaces:** exactly the "Index amendments" above. `config.Vlm` dataclass = spec §8.3 keys with those defaults; `load_config()` validation per §8.3 (fatal: unknown backend, `openai` with malformed `base_url`; warning: `remote_enabled` without `openai`, `api_key` inline, custom preset paths missing). `AidocConfig.vlm: Vlm`; `_SECTIONS["vlm"] = Vlm`. `config.to_dict()` includes `vlm`; `settings.masked()` masks `vlm.api_key` like `server.token` (`"***"` when set).

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_models.py (append)
from pathlib import Path
from aidoc.models import ENGINE_NAMES, ConvertOptions, RawResult


def test_engine_names_gain_vlm_last():
    assert ENGINE_NAMES == ("markitdown", "docling", "mineru", "vlm")


def test_convert_options_vlm_remote_round_trip():
    o = ConvertOptions(output_dir=Path("out"), vlm_remote=False)
    assert ConvertOptions.from_json(o.to_json()).vlm_remote is False
    assert ConvertOptions(output_dir=Path("out")).vlm_remote is None


def test_raw_result_extra_default():
    assert RawResult(markdown="", image_paths=[], raw_dir=Path("."), has_page_markers=True).extra == {}
```

```python
# tests/unit/test_config.py (append)
import pytest
from aidoc.config import ConfigError, load_config


def _write(tmp_path, body):
    p = tmp_path / "aidoc.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_vlm_defaults_are_off(tmp_path):
    cfg = load_config(_write(tmp_path, ""))
    v = cfg.vlm
    assert v.backend == "none" and v.remote_enabled is False and v.use_as_fallback and v.use_for_repair
    assert v.api_key_env == "AIDOC_VLM_API_KEY" and v.remote_daily_page_budget == 500 and v.local_preset == "paddleocr-vl-1.6"


def test_vlm_openai_requires_clean_base_url(tmp_path):
    with pytest.raises(ConfigError, match="base_url"):
        load_config(_write(tmp_path, '[vlm]\nbackend = "openai"\nbase_url = "http://user:key@host/v1"\nmodel = "m"\n'))
    with pytest.raises(ConfigError, match="base_url"):
        load_config(_write(tmp_path, '[vlm]\nbackend = "openai"\nbase_url = "host/v1"\nmodel = "m"\n'))
    with pytest.raises(ConfigError, match="model"):
        load_config(_write(tmp_path, '[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:1234/v1"\n'))


def test_vlm_unknown_backend_is_fatal(tmp_path):
    with pytest.raises(ConfigError, match="backend"):
        load_config(_write(tmp_path, '[vlm]\nbackend = "paddle"\n'))


def test_vlm_remote_enabled_without_openai_warns(tmp_path):
    cfg = load_config(_write(tmp_path, '[vlm]\nbackend = "none"\nremote_enabled = true\n'))
    assert cfg.vlm.remote_enabled is False and any("remote_enabled" in w for w in cfg.warnings)


def test_vlm_inline_key_warns(tmp_path):
    cfg = load_config(_write(tmp_path, '[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:1/v1"\nmodel = "m"\napi_key = "sk-x"\n'))
    assert any("AIDOC_VLM_API_KEY" in w for w in cfg.warnings)
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_models.py tests/unit/test_config.py -q` → FAIL (`ENGINE_NAMES` has 3 entries; `unknown config section vlm`).
- [ ] **Step 3: Implement** `models.py` (tuple, `vlm_remote`, `RawResult.extra`), `config.Vlm` + `_check_vlm(cfg, where)` (called from `load_config` next to `_check_mcp`), `settings.py` `_CHOICES[("vlm","backend")] = {"none","local","openai"}`, `_CHOICES[("vlm","local_preset")] = {"paddleocr-vl-1.6","qwen2.5-vl-3b","custom"}`, `_MIN`/`_MAX` per spec §8.3, `api_key` masking + `"***"` echo rule (copy the `server.token` branch but allow writes), `remote_enabled: true` with `backend != "openai"` → 422. Apply every bullet of "Index amendments" to the index verbatim.
- [ ] **Step 4: Run** `uv run pytest tests/unit -q` → every test passes **except** tests that enumerate engines by count (`/api/system` engine keys, Settings `ENGINES` list in Vitest are untouched until Task 10/11; Python tests asserting `len(ENGINE_NAMES) == 3` or iterating registry classes will fail with `KeyError: 'vlm'` in `registry._CLASSES`). Record each failing test name; Task 5 fixes the registry. Do not weaken tests.
- [ ] **Step 5: Commit** `feat(contracts): vlm engine name, [vlm] config, settings whitelist, index amendments (spec 2026-10-05 VLM)`.

---

### Task 1: Spike S1 — PaddleOCR-VL-1.6 on this machine (llama.cpp GGUF first), with the decision rule

**Files:**
- Create: `docs/superpowers/spikes/2026-10-vlm-ocr.md`; scratch scripts under `.superpowers/spike/` (gitignored)

**Spec:** §3.2 (feasibility facts), §3.4 (samples, measurements, decision rule). Time-box: one working day; running out of time is decision rule 4.

- [ ] **Step 1: Path (b) setup, by hand, recording every command** — download the pinned llama.cpp Windows `cuda-13` prebuilt (tag ≥ b8110; record the exact tag and both zip sha256s), the `cudart` zip, `PaddlePaddle/PaddleOCR-VL-1.6-GGUF` (F16 and Q8_0 model files + mmproj; record exact file names and sha256). Start `llama-server -m <gguf> --mmproj <mmproj> --host 127.0.0.1 --port 8089 -ngl 99 -c 8192 --temp 0` and capture the startup log (CUDA device line, offloaded layers). Record the exact name of the image-pixel-limit flag from `llama-server --help`.
- [ ] **Step 2: Measurement script** `.superpowers/spike/vlm_s1_measure.py`: renders each sample page with PyMuPDF at 200 dpi (long side ≤ 1400 px), POSTs one Chat Completions request per page with prompt style `paddle_tasks` (`"OCR:"`), records per-page seconds, `finish_reason`, `usage`, and computes char-multiset recall against the fixture strings (`tests/fixtures/make_fixtures.py` `ZH`/`EN`) or, for real samples, against the existing `ok` MinerU output in the library (`squash` both, recall = common chars / expected chars). Samples: spec §3.4 list. Sample `nvidia-smi --query-gpu=memory.used --format=csv,noheader` every 0.5 s in a thread for peak VRAM.
- [ ] **Step 3: Variants** — (i) `"Table Recognition:"` on the whole `text.pdf` page 2 and on a cropped table; (ii) `Qwen2.5-VL-3B-Instruct` GGUF + the §4.4 generic prompt as the control; (iii) Q8_0 vs F16 for PaddleOCR-VL.
- [ ] **Step 4: Apply the decision rule** (spec §3.4) and write the write-up with: environment line, the table of per-sample recall / seconds / peak VRAM per variant, the `hfad_cover_p1` phrase hit, the table/formula observations, the exact pinned tags/URLs/sha256s, the flag names, and a one-paragraph **Decision** naming the task to execute (8a / 8b / 8c) and the default `local_preset` and `prompt_style`. If rule 1's table clause selects the two-pass table mode, say so explicitly.
- [ ] **Step 5: Commit** `docs(spike): PaddleOCR-VL-1.6 via llama.cpp on RTX 5070 Ti — feasibility, quality, decision`.

---

### Task 2 (optional): Spike S2 — shape differences of real external endpoints

**Files:** append to `docs/superpowers/spikes/2026-10-vlm-ocr.md` a section "S2 endpoint shapes".

- [ ] **Step 1:** Against whichever of LM Studio / Ollama `/v1` / the Task 1 `llama-server` is installed, send one page with the exact request body `vlm_runner.py` will send (data-URI `image_url`, `detail` present and absent, `max_tokens` + `max_completion_tokens` both present). Record: accepted shape, `usage` presence, `finish_reason` values, fenced-Markdown wrapping, 404 body for an unknown model.
- [ ] **Step 2:** Turn each observed quirk into a fake-endpoint behaviour name for Task 3 (`ok_fenced`, `no_usage`, …) and list them in the write-up. No decision rule.
- [ ] **Step 3: Commit** `docs(spike): S2 endpoint shape notes`.

### Task 3: Fake OpenAI-compatible VLM endpoint (`tests/fakes/vlm_server.py`)

**Files:**
- Create: `tests/fakes/vlm_server.py`, `tests/unit/test_fake_vlm_server.py`

**Interfaces:**
- `class FakeVlmServer(scenario: dict | Path, *, host="127.0.0.1")` with `.start() -> str` (returns `base_url` = `http://127.0.0.1:<port>/v1`), `.stop()`, `.calls: list[dict]` and `calls.jsonl` written next to the scenario (`call_log` key). Context-manager support.
- Routes: `GET /v1/models` → `{"data": [{"id": <model>}]}`; `POST /v1/chat/completions` → behaviour by scenario. Anything else 404.
- Scenario (JSON): `{"default": "ok", "model": "fake-vlm", "delay_s": 0, "pages": {"3": "text for page 3"}, "require_key": "sk-test", "rules": [{"match": {"page": 3, "request_no": 1, "model": "x"}, "behavior": "refuse"}]}`. `request_no` counts requests for that page.
- Behaviours (spec §11.1): `ok` (content `# Page {k}\n\n{pages[k] or "假端點產生的內容 fake vlm content for page k."}` + a 2-col GFM table when `with_table` is set), `ok_fenced`, `blank` (`<!-- blank -->`), `refuse`, `length` (half the text, `finish_reason: "length"`), `rate_limit` (429, `Retry-After: 1`), `server_error` (500), `unauthorized` (401), `model_missing` (404 with OpenAI-style error body), `bad_request` (400), `slow` (sleep `delay_s` then `ok`), `hang` (never answers; the handler blocks until `.stop()`), `no_usage` (ok without `usage`).
- Each call record: `{"ts", "path", "model", "page", "total", "image_bytes", "image_px": [w, h], "has_auth", "detail", "max_tokens", "max_completion_tokens"}` — `page`/`total` parsed from the user text `Page k of n`; `image_px` decoded from the PNG header (stdlib: read IHDR), no Pillow.
- `require_key`: when set, a request without `Authorization: Bearer <key>` → 401 regardless of behaviour.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_fake_vlm_server.py
import base64, json, struct, urllib.request, zlib
from tests.fakes.vlm_server import FakeVlmServer


def _png(w=40, h=30):
    raw = b"".join(b"\x00" + b"\xff" * (w * 3) for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _post(base, body, key=None):
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers)


def _body(page=3, total=10, model="fake-vlm"):
    uri = "data:image/png;base64," + base64.b64encode(_png()).decode()
    return {"model": model, "max_tokens": 100, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": uri, "detail": "high"}},
        {"type": "text", "text": f"Page {page} of {total}. Return the Markdown of this page."}]}]}


def test_ok_uses_page_text_and_logs_image_size(tmp_path):
    sc = {"default": "ok", "pages": {"3": "第三頁的內容"}, "call_log": str(tmp_path / "calls.jsonl")}
    with FakeVlmServer(sc) as base:
        status, out, _ = _post(base, _body())
    assert status == 200 and "第三頁的內容" in out["choices"][0]["message"]["content"]
    assert out["choices"][0]["finish_reason"] == "stop" and out["usage"]["prompt_tokens"] > 0
    call = json.loads((tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert call["page"] == 3 and call["total"] == 10 and call["image_px"] == [40, 30] and call["has_auth"] is False


def test_rules_by_page_and_request_no(tmp_path):
    sc = {"default": "ok", "rules": [{"match": {"page": 3, "request_no": 1}, "behavior": "rate_limit"},
                                     {"match": {"page": 4}, "behavior": "length"}]}
    with FakeVlmServer(sc) as base:
        s1, _, h1 = _post(base, _body(3)); s2, _, _ = _post(base, _body(3)); s3, o3, _ = _post(base, _body(4))
    assert (s1, s2, s3) == (429, 200, 200) and h1.get("Retry-After") == "1"
    assert o3["choices"][0]["finish_reason"] == "length"


def test_require_key_and_errors(tmp_path):
    with FakeVlmServer({"default": "ok", "require_key": "sk-test"}) as base:
        assert _post(base, _body())[0] == 401
        assert _post(base, _body(), key="sk-test")[0] == 200
    with FakeVlmServer({"default": "model_missing"}) as base:
        status, out, _ = _post(base, _body())
    assert status == 404 and out["error"]["code"] == "model_not_found"


def test_models_endpoint():
    with FakeVlmServer({"default": "ok", "model": "fake-vlm"}) as base:
        with urllib.request.urlopen(base + "/models", timeout=5) as r:
            assert json.loads(r.read())["data"][0]["id"] == "fake-vlm"
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_fake_vlm_server.py -q` → FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** with `http.server.ThreadingHTTPServer` on a daemon thread, `server.server_address[1]` for the port, `hang` implemented by waiting on a `threading.Event` that `.stop()` sets. The error bodies follow OpenAI's `{"error": {"message", "type", "code"}}` shape (`model_not_found`, `invalid_api_key`, `rate_limit_exceeded`, `server_error`, `invalid_request_error`).
- [ ] **Step 4: Run** → pass. Also `uv run ruff check tests/fakes/vlm_server.py`.
- [ ] **Step 5: Commit** `test(fakes): scripted OpenAI-compatible VLM endpoint for unit/API/reliability tests`.

---

### Task 4: `vlm_runner.py` — pages → requests → cleaned Markdown (remote path), prompt module

**Files:**
- Create: `src/aidoc/engines/runner/_vlm_prompt.py`, `src/aidoc/engines/runner/vlm_runner.py`, `tests/unit/test_vlm_runner.py`

**Interfaces (functions are importable from the runner module for tests; `handle(req)` is the `_proto.serve` entry):**
- `_vlm_prompt.PROMPT_VERSION = 1`; `SYSTEM = {"cht": str, "en": str}`; `USER = "Page {k} of {n}. Return the Markdown of this page."`; `PADDLE_TASKS = {"ocr": "OCR:", "table": "Table Recognition:", "formula": "Formula Recognition:"}`; `prompt_sha256() -> str`; `messages(style: str, lang: str, data_uri: str, k: int, n: int, detail: str | None) -> list[dict]`.
- `render_page(doc, index: int, dpi: int, max_px: int) -> tuple[bytes, tuple[int, int], str]` → (bytes, (w, h), mime) — PNG, long side ≤ `max_px`, JPEG q=85 when PNG > `max_image_bytes` (15 MB).
- `clean_markdown(text: str) -> tuple[str, str | None]` → (cleaned, flag) where flag ∈ {None, "blank", "refused"}: strips one outer ```` ```markdown ```` / ```` ``` ```` fence, strips known trailer sentences (`Let me know if…`, `Is there anything else…`, `希望這對您有幫助`), maps `<!-- blank -->` → ("", "blank"), detects refusals (regex list in the module, text < 200 chars) → ("", "refused").
- `class Client(base_url, model, api_key: str | None, timeout_s, detail: str | None, max_output_tokens)` with `.chat(messages, max_tokens) -> dict` raising `HttpStatus(code, retry_after, body)`; `.models() -> list[str]`.
- `send_page(client, png, k, n, opts) -> PageOut(markdown, flag, finish_reason, usage, attempts, dpi_used)`: retry schedule per spec §4.4 (429/5xx/connection/timeout → `REMOTE_RETRY_BACKOFF`, `Retry-After` honoured ≤ 120 s; 401/403/404 → `_proto.RunnerError("engine", …)`; 400 → re-render at `dpi − 50` once (≥ 72); `finish_reason == "length"` → retry once with `max_tokens × 2` (≤ 16384), still length → keep partial text, flag `"truncated"`). Sleeping goes through an injectable `sleep` for tests.
- `handle(req)`: reads `engine_opts`, resolves the key (`os.environ[api_key_env]` → `os.environ.get("AIDOC_VLM_API_KEY_INLINE")` → None; if None and not `allow_no_key` → `RunnerError("engine", "vlm: no API key …")`), opens the PDF/image, runs pages with `ThreadPoolExecutor(max_concurrent)`, assembles `<!-- page: k -->\n\n{md}\n\n` for every page (blank pages included), writes `out.md`, returns `result.json` fields + `vlm` block (spec §4.2). Page flags: `refused`/`truncated` → `failed_pages[k]` = `"refused"` / `"truncated"` (truncated keeps the partial section), `refused_pages`/`truncated_pages` lists. `images`: `[]`; `<!-- image -->` lines are rewritten to `<!-- image missing -->` and counted in `images_missing[k]`. Progress per page via `_proto.progress`. `GET /models` once at start (10 s, failures logged only) then `_proto.ready()` — **note**: `_proto.serve()` calls `ready()` before the first request; for the remote backend that is acceptable (no model load). Task 4b restructures `main()` so the local backend emits ready only after `/health`.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_vlm_runner.py
import json, os
from pathlib import Path
import pytest
from aidoc.engines.runner import _vlm_prompt as P
from aidoc.engines.runner import vlm_runner as R
from tests.fakes.vlm_server import FakeVlmServer

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def test_prompt_version_is_pinned_to_text():
    # changing any prompt text must bump PROMPT_VERSION and this constant
    assert P.PROMPT_VERSION == 1
    assert P.prompt_sha256() == P.PINNED_SHA256


def test_messages_shapes():
    m = P.messages("generic", "cht", "data:image/png;base64,AAAA", 2, 9, detail="high")
    assert m[0]["role"] == "system" and "Traditional Chinese" in m[0]["content"]
    parts = m[1]["content"]
    assert parts[0]["type"] == "image_url" and parts[0]["image_url"]["detail"] == "high"
    assert parts[1]["text"].startswith("Page 2 of 9")
    t = P.messages("paddle_tasks", "cht", "data:image/png;base64,AAAA", 1, 1, detail=None)
    assert len(t) == 1 and t[0]["content"][1]["text"] == "OCR:" and "detail" not in t[0]["content"][0]["image_url"]


def test_render_page_respects_max_px():
    import pymupdf
    with pymupdf.open(FIX / "text.pdf") as doc:
        png, (w, h), mime = R.render_page(doc, 0, dpi=200, max_px=900)
    assert mime == "image/png" and max(w, h) <= 900 and png[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("text,expect", [
    ("```markdown\n# T\n\nbody\n```", ("# T\n\nbody", None)),
    ("# T\n\nbody\n\nLet me know if you need anything else!", ("# T\n\nbody", None)),
    ("<!-- blank -->", ("", "blank")),
    ("I'm sorry, I can't help with that.", ("", "refused")),
    ("抱歉，我無法辨識這張圖片。", ("", "refused")),
])
def test_clean_markdown(text, expect):
    assert R.clean_markdown(text) == expect


def _opts(base, **kw):
    return {"backend": "openai", "base_url": base, "model": "fake-vlm", "api_key_env": "AIDOC_TEST_VLM_KEY",
            "dpi": 100, "max_image_px": 800, "request_timeout_s": 5, "max_concurrent": 2, "max_output_tokens": 256,
            "detail": "high", "prompt_version": 1, "prompt_style": "generic", "lang": "cht", **kw}


def _req(tmp_path, base, src=FIX / "text.pdf", **kw):
    return {"src": str(src), "out_dir": str(tmp_path / "raw"), "lang": "cht", "kind": "pdf", "pages": None,
            "segment_idx": 0, "engine_opts": _opts(base, **kw)}


def test_handle_marks_every_page_and_reports_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "sk-test")
    sc = {"default": "ok", "require_key": "sk-test", "pages": {"2": "第二頁 second page"}, "call_log": str(tmp_path / "calls.jsonl")}
    with FakeVlmServer(sc) as base:
        res = R.handle(_req(tmp_path, base))
    md = Path(res["markdown_path"]).read_text(encoding="utf-8")
    assert md.count("<!-- page: ") == 3 and "第二頁 second page" in md
    assert res["has_page_markers"] and res["page_map_method"] == "vlm_per_page" and res["page_count"] == 3
    v = res["vlm"]
    assert v["backend"] == "openai" and v["pages_sent"] == 3 and v["usage"]["requests"] == 3 and v["usage"]["prompt_tokens"] > 0
    assert v["endpoint_host"].startswith("127.0.0.1:") and v["prompt_version"] == 1
    calls = [json.loads(l) for l in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(c["has_auth"] for c in calls) and all(max(c["image_px"]) <= 800 for c in calls)
    assert "sk-test" not in json.dumps(res)                      # the key never reaches result.json


def test_refusal_truncation_and_blank(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "k")
    sc = {"default": "ok", "rules": [{"match": {"page": 1}, "behavior": "refuse"},
                                     {"match": {"page": 2}, "behavior": "length"},
                                     {"match": {"page": 3}, "behavior": "blank"}]}
    with FakeVlmServer(sc) as base:
        res = R.handle(_req(tmp_path, base))
    assert res["failed_pages"] == {"1": "refused", "2": "truncated"}
    assert res["vlm"]["refused_pages"] == [1] and res["vlm"]["truncated_pages"] == [2]
    md = Path(res["markdown_path"]).read_text(encoding="utf-8")
    assert md.count("<!-- page: ") == 3                          # the blank page keeps its marker, empty section


def test_retry_schedule_and_retry_after(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "k")
    slept = []
    monkeypatch.setattr(R, "_sleep", lambda s: slept.append(s))
    sc = {"default": "ok", "rules": [{"match": {"page": 1, "request_no": 1}, "behavior": "rate_limit"},
                                     {"match": {"page": 1, "request_no": 2}, "behavior": "server_error"}]}
    with FakeVlmServer(sc) as base:
        res = R.handle(_req(tmp_path, base, max_concurrent=1))
    assert res["failed_pages"] == {} and slept[0] == 1 and slept[1] == R.REMOTE_RETRY_BACKOFF[1]


def test_auth_error_is_engine_error_not_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "wrong")
    with FakeVlmServer({"default": "ok", "require_key": "right"}) as base:
        with pytest.raises(R._proto.RunnerError) as e:
            R.handle(_req(tmp_path, base))
    assert e.value.kind == "engine" and "401" in str(e.value)


def test_missing_key_is_engine_error(tmp_path, monkeypatch):
    monkeypatch.delenv("AIDOC_TEST_VLM_KEY", raising=False)
    monkeypatch.delenv("AIDOC_VLM_API_KEY_INLINE", raising=False)
    with FakeVlmServer({"default": "ok"}) as base:
        with pytest.raises(R._proto.RunnerError, match="API key"):
            R.handle(_req(tmp_path, base))
        assert R.handle(_req(tmp_path, base, allow_no_key=True))["page_count"] == 3


def test_bad_request_rerenders_at_lower_dpi(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TEST_VLM_KEY", "k")
    sc = {"default": "ok", "rules": [{"match": {"page": 1, "request_no": 1}, "behavior": "bad_request"}], "call_log": str(tmp_path / "c.jsonl")}
    with FakeVlmServer(sc) as base:
        res = R.handle(_req(tmp_path, base, dpi=150))
    calls = [json.loads(l) for l in (tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines() if '"page": 1' in l]
    assert res["failed_pages"] == {} and max(calls[1]["image_px"]) < max(calls[0]["image_px"])
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_vlm_runner.py -q` → FAIL (import errors).
- [ ] **Step 3: Implement** `_vlm_prompt.py` (compute `PINNED_SHA256` once by running `prompt_sha256()` and paste the value), then `vlm_runner.py`. Keep page order deterministic: results stored by index, assembled after the pool completes. The `vlm` block's `endpoint_host` is `urlparse(base_url).netloc` (no path, no credentials — the config check already forbids `@`).
- [ ] **Step 4: Run** → pass; `uv run ruff check src/aidoc/engines/runner/`.
- [ ] **Step 5: Commit** `feat(vlm): HTTP runner for OpenAI-compatible vision endpoints — prompts, rendering, retries, cleaning`.

---

### Task 4b: Local backend lifecycle in the runner — spawn, health, ready, shutdown (fake `llama-server`)

**Files:**
- Create: `tests/fakes/fake_llama_server.py`, `tests/unit/test_vlm_local_server.py`
- Modify: `src/aidoc/engines/runner/vlm_runner.py` (`main()`, `LocalServer`), `src/aidoc/procs.py` (`is_aidoc_runner` recognises a `llama-server` whose cwd or `-m` path is under `data/`), `tests/unit/test_procs.py`

**Interfaces:**
- `class LocalServer(exe: str, gguf: str, mmproj: str, ctx_size: int, image_max_pixels: int, startup_timeout_s: float, log=_proto.log)`: `.start() -> str` (base_url on a free loopback port; pumps the child's stdout/stderr into runner log lines prefixed `llama-server:`; raises `RunnerError("transient", …)` on exit before health or health timeout; converts OOM strings so `_proto.is_oom_text()` is true), `.alive() -> bool`, `.stop()` (terminate → wait 5 s → kill), `.pid`.
- `main()`: reads the first request path from stdin **before** `ready()`? No — keep `_proto.serve()`'s contract (ready before the loop). Instead the runner learns the backend from an env var the host sets: `AIDOC_VLM_BACKEND=local` plus `AIDOC_VLM_LOCAL_JSON=<path>` (the local engine_opts). When `local`, `main()` starts `LocalServer` first, then calls `_proto.serve(handle)`, so `AIDOC_READY` follows `/health`. On `serve()` return (stdin EOF) or any exception → `server.stop()` in `finally`. `atexit` and `SIGTERM`-less Windows: the host's `kill_tree()` covers the hard-kill case.
- Fake `llama-server` (`tests/fakes/fake_llama_server.py`): a Python script accepting the same CLI flags (`-m`, `--mmproj`, `--host`, `--port`, `-ngl`, `-c`, `--temp`, the pixel flag), serving `GET /health` (503 while "loading" for `FAKE_LLAMA_LOAD_S` seconds, then 200) and `POST /v1/chat/completions` by delegating to `FakeVlmServer`'s handler with scenario `FAKE_LLAMA_SCENARIO`; modes via env: `FAKE_LLAMA_DIE_AT_START=1` (exit 1 after printing `cudaMalloc failed: out of memory`), `FAKE_LLAMA_DIE_AFTER_REQUESTS=N`. Prints a CUDA-like startup line `load_tensors: offloaded 24/24 layers to GPU` so the self-check parser (Task 8a) can be tested.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_vlm_local_server.py
import json, os, subprocess, sys, time
from pathlib import Path
import psutil, pytest
from aidoc.engines.runner import vlm_runner as R

FAKE = Path(__file__).resolve().parents[1] / "fakes" / "fake_llama_server.py"
FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _server(tmp_path, **env):
    exe = [sys.executable, str(FAKE)]
    os.environ.update({k: str(v) for k, v in env.items()})
    return R.LocalServer(exe=exe, gguf=str(tmp_path / "m.gguf"), mmproj=str(tmp_path / "p.gguf"), ctx_size=4096,
                         image_max_pixels=1003520, startup_timeout_s=10)


def test_start_waits_for_health_then_serves(tmp_path, monkeypatch):
    (tmp_path / "sc.json").write_text(json.dumps({"default": "ok"}), encoding="utf-8")
    monkeypatch.setenv("FAKE_LLAMA_SCENARIO", str(tmp_path / "sc.json"))
    monkeypatch.setenv("FAKE_LLAMA_LOAD_S", "0.5")
    s = _server(tmp_path)
    t0 = time.time(); base = s.start()
    assert time.time() - t0 >= 0.5 and base.startswith("http://127.0.0.1:") and s.alive()
    client = R.Client(base, "local", None, 5, None, 256)
    assert client.models()
    pid = s.pid; s.stop()
    assert not psutil.pid_exists(pid) or not psutil.Process(pid).is_running()


def test_death_at_start_is_transient_with_oom_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA_DIE_AT_START", "1")
    with pytest.raises(R._proto.RunnerError) as e:
        _server(tmp_path).start()
    assert e.value.kind == "transient" and R._proto.is_oom_text(str(e.value))


def test_runner_process_kills_server_on_stdin_eof(tmp_path, monkeypatch):
    (tmp_path / "sc.json").write_text(json.dumps({"default": "ok"}), encoding="utf-8")
    local = {"backend": "local", "server_exe": [sys.executable, str(FAKE)], "gguf": "x", "mmproj": "y", "ctx_size": 4096,
             "image_max_pixels": 1003520, "startup_timeout_s": 10}
    (tmp_path / "local.json").write_text(json.dumps(local), encoding="utf-8")
    env = {**os.environ, "AIDOC_VLM_BACKEND": "local", "AIDOC_VLM_LOCAL_JSON": str(tmp_path / "local.json"),
           "FAKE_LLAMA_SCENARIO": str(tmp_path / "sc.json"), "PYTHONUTF8": "1"}
    p = subprocess.Popen([sys.executable, str(R.__file__)], stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=tmp_path)
    line = p.stderr.readline().decode()
    while not line.startswith("AIDOC_READY"):
        line = p.stderr.readline().decode()
        assert line, "runner died before READY"
    children = psutil.Process(p.pid).children(recursive=True)
    assert children, "llama-server child not running"
    p.stdin.close(); p.wait(timeout=15)
    time.sleep(0.5)
    assert not any(c.is_running() for c in children)
```

```python
# tests/unit/test_procs.py (append)
def test_is_aidoc_runner_recognises_llama_server_under_data(tmp_path):
    from aidoc.procs import _looks_like_aidoc_runner
    assert _looks_like_aidoc_runner(["C:/x/data/tools/llamacpp/b8110/llama-server.exe", "-m", "C:/x/data/models/vlm/a.gguf"], str(tmp_path))
    assert not _looks_like_aidoc_runner(["C:/other/llama-server.exe", "-m", "C:/other/a.gguf"], str(tmp_path))
```

- [ ] **Step 2: Run** → FAIL (`LocalServer` missing; `_looks_like_aidoc_runner` missing — if `procs.is_aidoc_runner()` currently inlines the check, extract the pure predicate first).
- [ ] **Step 3: Implement.** `LocalServer.start()` picks a free port by binding a socket to port 0 and closing it, launches with `procs.popen_kwargs()`, pumps output on a thread, polls `/health` every 0.25 s; the OOM conversion appends `" (OutOfMemoryError)"` to the message when the pumped tail contains `cudaMalloc failed` / `out of memory`.
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_vlm_local_server.py tests/unit/test_procs.py -q` → pass (Windows: `psutil` child enumeration works for the Python fake).
- [ ] **Step 5: Commit** `feat(vlm): local backend lifecycle — runner-managed llama-server with health wait, OOM mapping, clean shutdown`.

### Task 5: `VlmEngine`, registry, fake engine, router

**Files:**
- Create: `src/aidoc/engines/vlm.py`, `tests/unit/test_vlm_engine.py`
- Modify: `src/aidoc/engines/registry.py`, `src/aidoc/engines/fake.py`, `src/aidoc/engines/runner/fake_runner.py`, `src/aidoc/router.py`, `src/aidoc/models.py` (`RouteDecision.missing` entries may be `"vlm(page_cap)"` / `"vlm(budget)"`), `tests/unit/test_router.py`, `tests/unit/test_registry.py`

**Interfaces:**
- `class VlmEngine(RunnerEngine)`: `name = "vlm"`; `python()` → `Path(sys.executable)` (spike path (c) would override: `paths.venv_python("vlm")`); `script()` → `paths.runner_script("vlm")`; `available()` per spec §4.1; `supports(probe)` → pdf/image; `engine_opts(opts, probe)` → spec §4.2 shape for the configured backend (local: paths from `envs/vlm/manifest.json`; `prompt_style` from preset or override; `table_pages` filled from `probe` only when the spike selected two-pass tables); `env_extra()` → `{"AIDOC_VLM_BACKEND": backend, "AIDOC_VLM_LOCAL_JSON": <path written under data/work/vlm_local.json>, "AIDOC_VLM_API_KEY_INLINE": cfg.vlm.api_key}` (inline key only when set; never in request.json); `oom_downgrade(eo)` → local: `max_image_px 1400→1000`, then `ctx_size 8192→4096`, then None; remote: None; `timeout_factor(pages, timeout)` → remote: `max(timeout, pages × request_timeout_s / max_concurrent × 1.5)` (used by pipeline Task 7); `label()` → `"vlm:local:<preset>"` / `"vlm:openai:<model-sanitised>"`; `is_remote()`; `remote_allowed(opts)` → `cfg.vlm.remote_enabled and opts.vlm_remote is not False`.
- `registry._CLASSES["vlm"] = VlmEngine`; `get_engines()` returns four engines; `FakeEngine("vlm")` works (`_real()` builds `VlmEngine`, whose `engine_opts` must not touch the filesystem when `backend == "none"` — return `{"backend": "none"}`).
- `fake_runner.py`: `engine_opts.fake_engine == "vlm"` behaves like the others; new behaviours `refuse` (all pages `failed_pages[k]="refused"`, sections empty) and `length` (half text, `failed_pages[k]="truncated"`), both only when `fake_engine == "vlm"`; `page_map_method` for the fake vlm is `vlm_per_page`; the `_write_ok` result gains `"vlm": {...}` when `fake_engine == "vlm"` (backend from `engine_opts.backend` or `"openai"`, `pages_sent = n`, `usage.requests = n`).
- `router.route(probe, opts, available, *, vlm_gate: Callable[[ProbeResult], str | None] | None = None)`: after `_rule()` the order gains `"vlm"` at the end for pdf/image when `available.get("vlm")`; `vlm_gate(probe)` (supplied by the pipeline: returns `"page_cap"` / `"budget"` / `"disabled_remote"` or None) removes it and appends `f"vlm({reason})"` to `missing`. With `available["vlm"]` false or absent → **identical** output to today (including `missing` not mentioning vlm — it is not "not installed", it is "not configured"; only when `backend != "none"` but not ready does `missing` get `"vlm"`, which the pipeline logs as today). `engine_supports("vlm", probe)` → pdf/image.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_router.py (append)
ALL4 = {**ALL, "vlm": True}
NO_VLM = {**ALL, "vlm": False}


@pytest.mark.parametrize("probe,expected,reason", [
    (ProbeResult(kind="office", ext=".docx", size=1), ["markitdown", "docling"], "non_pdf"),
    (ProbeResult(kind="image", ext=".png", size=1), ["mineru", "docling", "vlm"], "image"),
    (pdf(text_ratio=0.2), ["mineru", "docling", "vlm"], "pdf_scanned"),
    (pdf(text_ratio=1.0, math_hint=True), ["mineru", "docling", "vlm"], "pdf_math"),
    (pdf(text_ratio=1.0), ["docling", "mineru", "markitdown", "vlm"], "pdf_default"),
])
def test_vlm_is_always_last(probe, expected, reason):
    d = route(probe, opts(), ALL4)
    assert d.engines == expected and d.reason == reason


def test_vlm_absent_changes_nothing():
    for probe in [pdf(text_ratio=0.2), pdf(text_ratio=1.0), pdf(math_hint=True), ProbeResult(kind="image", ext=".png", size=1)]:
        assert route(probe, opts(), NO_VLM) == route(probe, opts(), ALL)
        assert "vlm" not in route(probe, opts(), ALL).missing


def test_vlm_gate_moves_it_to_missing_with_reason():
    d = route(pdf(text_ratio=0.2), opts(), ALL4, vlm_gate=lambda p: "page_cap")
    assert d.engines == ["mineru", "docling"] and d.missing == ["vlm(page_cap)"]


def test_forced_vlm():
    assert route(pdf(text_ratio=1.0), opts(engine="vlm"), ALL4).engines == ["vlm"]
    assert route(ProbeResult(kind="office", ext=".docx", size=1), opts(engine="vlm"), ALL4).reason == "engine_unsupported"
    assert engine_supports("vlm", ProbeResult(kind="image", ext=".png", size=1))
```

```python
# tests/unit/test_vlm_engine.py
import json, sys
from pathlib import Path
from aidoc.config import load_config
from aidoc.engines.vlm import VlmEngine
from aidoc.models import ConvertOptions, ProbeResult


def _cfg(tmp_path, body):
    p = tmp_path / "aidoc.toml"; p.write_text(body, encoding="utf-8"); return load_config(p)


def test_none_backend_is_unavailable_and_inert(tmp_path):
    e = VlmEngine(_cfg(tmp_path, ""))
    assert not e.available() and e.engine_opts(ConvertOptions(output_dir=tmp_path), ProbeResult(kind="pdf", ext=".pdf", size=1)) == {"backend": "none"}
    assert e.python() == Path(sys.executable) and e.script().name == "vlm_runner.py"


def test_openai_availability_needs_switch_and_key(tmp_path, monkeypatch):
    body = '[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:1234/v1"\nmodel = "m"\nremote_enabled = %s\n'
    monkeypatch.delenv("AIDOC_VLM_API_KEY", raising=False)
    assert not VlmEngine(_cfg(tmp_path, body % "false")).available()
    assert not VlmEngine(_cfg(tmp_path, body % "true")).available()          # no key
    monkeypatch.setenv("AIDOC_VLM_API_KEY", "k")
    e = VlmEngine(_cfg(tmp_path, body % "true"))
    assert e.available() and e.is_remote() and e.label() == "vlm:openai:m"
    eo = e.engine_opts(ConvertOptions(output_dir=tmp_path), ProbeResult(kind="pdf", ext=".pdf", size=1))
    assert eo["backend"] == "openai" and eo["api_key_env"] == "AIDOC_VLM_API_KEY" and "k" not in json.dumps(eo)
    assert e.oom_downgrade(eo) is None


def test_local_availability_needs_manifest_files(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_ENVS", str(tmp_path / "envs"))
    env = tmp_path / "envs" / "vlm"; env.mkdir(parents=True)
    exe, gguf, mm = tmp_path / "llama-server.exe", tmp_path / "m.gguf", tmp_path / "p.gguf"
    (env / "manifest.json").write_text(json.dumps({"runtime_tag": "b8110", "preset": "paddleocr-vl-1.6",
        "files": {"server_exe": str(exe), "gguf": str(gguf), "mmproj": str(mm)}}), encoding="utf-8")
    (env / ".ready").write_text("{}", encoding="utf-8")
    e = VlmEngine(_cfg(tmp_path, '[vlm]\nbackend = "local"\n'))
    assert not e.available()                                        # files missing
    for f in (exe, gguf, mm): f.write_bytes(b"x")
    assert e.available() and e.label() == "vlm:local:paddleocr-vl-1.6"
    eo = e.engine_opts(ConvertOptions(output_dir=tmp_path), ProbeResult(kind="pdf", ext=".pdf", size=1))
    assert eo["backend"] == "local" and eo["prompt_style"] == "paddle_tasks" and eo["max_image_px"] == 1400
    assert e.oom_downgrade(eo)["max_image_px"] == 1000 and e.oom_downgrade(e.oom_downgrade(eo))["ctx_size"] == 4096
    assert e.oom_downgrade(e.oom_downgrade(e.oom_downgrade(eo))) is None


def test_remote_timeout_factor(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_VLM_API_KEY", "k")
    e = VlmEngine(_cfg(tmp_path, '[vlm]\nbackend = "openai"\nbase_url = "http://h/v1"\nmodel = "m"\nremote_enabled = true\nrequest_timeout_s = 100\nmax_concurrent = 2\n'))
    assert e.timeout_factor(40, 2400) == 3000                        # 40 × 100 / 2 × 1.5
    assert e.timeout_factor(1, 120) == 120
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_router.py tests/unit/test_vlm_engine.py tests/unit/test_registry.py -q` → FAIL.
- [ ] **Step 3: Implement.** Registry iterates `ENGINE_NAMES` (already), add the class. Fake runner: look up `fake_engine == "vlm"` for the new behaviours; `tests/fixtures` unchanged.
- [ ] **Step 4: Run** `uv run pytest tests/unit -q` → the Task 0 "engine count" failures are now gone; any API test that lists engines fails only in Task 10's files (none yet). Record anything else.
- [ ] **Step 5: Commit** `feat(vlm): VlmEngine with local/openai backends; vlm is the last engine of every pdf/image route`.

---

### Task 6: Repair order gains V; VLM-specific acceptance

**Files:**
- Modify: `src/aidoc/repair.py`, `tests/unit/test_repair.py`

**Interfaces:**
- `repair_order(main_engine, available, *, engines: dict | None = None, allow_vlm: bool = True) -> list[tuple[str, dict, str]]`: existing A/B logic, then `("vlm", {}, engines["vlm"].label())` appended when `allow_vlm and available.get("vlm")`; `main_engine == "vlm"` → `[]`. (The engine_opts override for vlm is `{}`: the engine's own `engine_opts()` already encodes backend/preset; `repair_pages` merges `{**engine.engine_opts(...), **override}` as today.)
- `accept_repaired_section(section: str, label: str, failed: dict[int, str], page: int) -> bool`: existing rule (non-empty + `flag_page(sig, broken_font=True) == []`) plus, when `label.startswith("vlm:")`: `page not in failed` (refused/truncated) and `"<!-- blank -->" not in section`. `repair_pages()` uses it and reads `raw.failed_pages` from the VLM result (relative page k → `remaining[k-1]`).
- `repair_pages(ctx, engines, norm, pages, main_engine)` computes `allow_vlm = ctx.config.vlm.use_for_repair and ctx.vlm_budget_ok(len(remaining))` (hook on the context, Task 7 provides it; default True) and logs `repair: vlm skipped (budget)` when not.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_repair.py (append)
from aidoc.repair import accept_repaired_section, repair_order


class _E:  # minimal stand-in with a label
    def __init__(self, lbl): self._l = lbl
    def label(self): return self._l


AV = {"docling": True, "mineru": True, "vlm": True}
ENG = {"vlm": _E("vlm:openai:m")}


def test_repair_order_appends_vlm_last():
    assert [o[0] for o in repair_order("docling", AV, engines=ENG)] == ["docling", "mineru", "vlm"]
    assert [o[0] for o in repair_order("mineru", AV, engines=ENG)] == ["mineru", "docling", "vlm"]
    assert repair_order("docling", AV, engines=ENG)[-1][2] == "vlm:openai:m"
    assert repair_order("vlm", AV, engines=ENG) == []
    assert [o[0] for o in repair_order("docling", AV, engines=ENG, allow_vlm=False)] == ["docling", "mineru"]
    assert [o[0] for o in repair_order("docling", {**AV, "vlm": False}, engines=ENG)] == ["docling", "mineru"]


def test_vlm_repairs_reject_refused_truncated_and_blank():
    good = "Wouldn't it be dreamy if there was a book on Android that was easy to read. " * 3
    assert accept_repaired_section(good, "vlm:openai:m", {}, 1)
    assert not accept_repaired_section(good, "vlm:openai:m", {1: "truncated"}, 1)
    assert not accept_repaired_section("<!-- blank -->", "vlm:openai:m", {}, 1)
    assert not accept_repaired_section("", "docling:pypdfium_full_page_ocr", {}, 1)
    assert accept_repaired_section(good, "docling:pypdfium_full_page_ocr", {1: "x"}, 1)   # only vlm looks at failed
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests/unit/test_repair.py tests/unit/test_pipeline.py -q` → pass (the existing pipeline repair tests pass `engines` without vlm, so order is unchanged).
- [ ] **Step 5: Commit** `feat(repair): VLM is the last repair engine; refused/truncated/blank VLM pages are never accepted`.

---

### Task 7: Pipeline — gate, usage accounting, budget, sidecar `vlm` block, `options_key`, schema v6

**Files:**
- Modify: `src/aidoc/store.py` (schema v6 + `vlm_usage` methods), `src/aidoc/pipeline.py`, `src/aidoc/output.py` (`build_sidecar(..., vlm=None)`), `src/aidoc/engines/base.py` (`raw_from_result` fills `RawResult.extra["vlm"]`), `src/aidoc/server/maintenance.py` (`prune_vlm_usage`), `src/aidoc/cli.py` (`--no-vlm-remote`), `tests/unit/test_store.py`, `tests/unit/test_pipeline_vlm.py` (new)

**Interfaces:**
- Store: migration v5 → v6 creates `vlm_usage`; `add_vlm_usage(day, backend, host, pages, requests, prompt_tokens, completion_tokens)` (UPSERT add); `vlm_usage_today(backend, host) -> dict` (zeros when none; `day` = local date `time.strftime("%Y-%m-%d")`); `vlm_usage_days(n) -> list[dict]`; `prune_vlm_usage(keep_days=180) -> int`.
- `pipeline.options_key(opts, config=None)` adds `vlm_backend` and `vlm_model` (from config: `backend` + preset or model; `"none"` when off). Callers pass `ctx.config`.
- `PipelineContext.vlm_gate(probe) -> str | None`: `"disabled_remote"` when the engine is remote and `not engine.remote_allowed(opts)`; `"page_cap"` when remote and `probe.pages > remote_max_pages_per_doc > 0`; `"budget"` when remote and budget > 0 and `used_today + probe.pages > budget`; else None. `PipelineContext.vlm_budget_ok(n_pages) -> bool` for repair (same budget rule; local backend always True).
- `_run_task`: `route(..., vlm_gate=ctx.vlm_gate)`; before `run_engine_attempt` with a remote VLM: `ctx.log(f"vlm(openai): sending {pages} page image(s) to {host} model={model}")`. `run_engine_attempt` applies `engine.timeout_factor()` when present. After every VLM raw result (segment or repair): `store.add_vlm_usage(...)` from `raw.extra["vlm"]`.
- Sidecar: `vlm` block per spec §8.1 aggregated over segments (+ repair: `role`); `_Candidate` gains `vlm: dict | None`; repair outcome returns `vlm_usage` aggregated so the primary's sidecar can carry a `role: "repair"` block when the main engine is not vlm.
- CLI: `--no-vlm-remote` on convert/batch → `ConvertOptions.vlm_remote = False`.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_store.py (append)
def test_vlm_usage_upsert_and_today(tmp_path):
    from aidoc.store import Store
    s = Store(tmp_path / "a.db")
    assert s.schema_version() >= 6
    s.add_vlm_usage("2026-10-05", "openai", "h:1", pages=3, requests=3, prompt_tokens=10, completion_tokens=5)
    s.add_vlm_usage("2026-10-05", "openai", "h:1", pages=2, requests=2, prompt_tokens=1, completion_tokens=1)
    s.add_vlm_usage("2026-10-04", "openai", "h:1", pages=9, requests=9, prompt_tokens=0, completion_tokens=0)
    row = s.vlm_usage_days(30)
    assert {r["day"]: r["pages"] for r in row} == {"2026-10-05": 5, "2026-10-04": 9}
    assert s.vlm_usage_today("openai", "h:1", today="2026-10-05")["pages"] == 5
    assert s.vlm_usage_today("local", "local", today="2026-10-05")["pages"] == 0
    assert s.prune_vlm_usage(keep_days=0, today="2026-10-06") == 2
```

```python
# tests/unit/test_pipeline_vlm.py
import json
from pathlib import Path
import pytest
from aidoc.config import load_config
from aidoc.engines.registry import get_engines
from aidoc.models import ConvertOptions, TaskStatus
from aidoc.names import file_sha256
from aidoc.pipeline import options_key, run_task
from aidoc.store import Store
from tests.fakes.scenario import fake_env, write_scenario

VLM_ON = '[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "m"\nremote_enabled = true\nremote_daily_page_budget = %d\nremote_max_pages_per_doc = %d\n'


@pytest.fixture
def env(tmp_root, monkeypatch, fixtures):
    def make(cfg_body="", budget=500, cap=200):
        (tmp_root / "aidoc.toml").write_text(cfg_body or VLM_ON % (budget, cap), encoding="utf-8")
        monkeypatch.setenv("AIDOC_CONFIG", str(tmp_root / "aidoc.toml"))
        monkeypatch.setenv("AIDOC_VLM_API_KEY", "k")
        sc = write_scenario(tmp_root / "sc.json"); fake_env(monkeypatch, sc)
        cfg = load_config(); store = Store(tmp_root / "data" / "aidoc.db")
        def task(name, **opt):
            src = tmp_root / name; src.write_bytes((fixtures / name).read_bytes())
            opts = ConvertOptions(output_dir=tmp_root / "out", **opt)
            job = store.create_job(opts, "cli")
            tid, _ = store.create_task(job, str(src), file_sha256(src), src.stat().st_size, src.stat().st_mtime, opts.lang,
                                       str(tmp_root / "out" / Path(src).stem))
            return tid
        return {"cfg": cfg, "store": store, "sc": sc, "task": task, "root": tmp_root}
    return make


def test_all_three_low_falls_through_to_vlm(env):
    e = env()
    write_scenario(e["sc"], rules=[{"match": {"engine": n}, "behavior": "low"} for n in ("docling", "mineru", "markitdown")])
    tid = e["task"]("text.pdf")
    logs = []
    assert run_task(e["store"], tid, get_engines(e["cfg"]), e["cfg"], emit=lambda k, p: logs.append(p.get("line", ""))) == TaskStatus.done
    t = e["store"].get_task(tid)
    assert t["engine"] == "vlm" and [a["engine"] for a in t["tried"]] == ["docling", "mineru", "markitdown", "vlm"]
    sc = json.loads((e["root"] / "out" / "text" / "text.json").read_text(encoding="utf-8"))
    assert sc["vlm"]["backend"] == "openai" and sc["vlm"]["pages_sent"] == 3 and sc["vlm"]["role"] == "primary"
    assert any(line.startswith("vlm(openai): sending 3 page image(s) to 127.0.0.1:9") for line in logs)
    assert e["store"].vlm_usage_today("openai", "127.0.0.1:9")["pages"] == 3


def test_page_cap_and_budget_gate(env):
    e = env(budget=500, cap=2)
    write_scenario(e["sc"], rules=[{"match": {"engine": n}, "behavior": "low"} for n in ("docling", "mineru", "markitdown")])
    tid = e["task"]("text.pdf")                                   # 3 pages > cap 2
    logs = []
    assert run_task(e["store"], tid, get_engines(e["cfg"]), e["cfg"], emit=lambda k, p: logs.append(p.get("line", ""))) == TaskStatus.low
    assert any("vlm(page_cap)" in line for line in logs) and e["store"].get_task(tid)["engine"] != "vlm"
    e2 = env(budget=2, cap=200)
    e2["store"].add_vlm_usage(__import__("time").strftime("%Y-%m-%d"), "openai", "127.0.0.1:9", 2, 2, 0, 0)
    write_scenario(e2["sc"], rules=[{"match": {"engine": n}, "behavior": "low"} for n in ("docling", "mineru", "markitdown")])
    tid2 = e2["task"]("text.pdf"); logs2 = []
    assert run_task(e2["store"], tid2, get_engines(e2["cfg"]), e2["cfg"], emit=lambda k, p: logs2.append(p.get("line", ""))) == TaskStatus.low
    assert any("vlm(budget)" in line for line in logs2)


def test_no_vlm_remote_option_disables_it_for_one_run(env):
    e = env()
    write_scenario(e["sc"], rules=[{"match": {"engine": n}, "behavior": "low"} for n in ("docling", "mineru", "markitdown")])
    tid = e["task"]("text.pdf", vlm_remote=False); logs = []
    assert run_task(e["store"], tid, get_engines(e["cfg"]), e["cfg"], emit=lambda k, p: logs.append(p.get("line", ""))) == TaskStatus.low
    assert any("vlm(disabled_remote)" in line for line in logs)


def test_options_key_includes_vlm_backend_and_model(env):
    e = env()
    k1 = options_key(ConvertOptions(output_dir=Path("o")), e["cfg"])
    e["cfg"].vlm.model = "other"
    assert options_key(ConvertOptions(output_dir=Path("o")), e["cfg"]) != k1
    assert "vlm_remote" not in k1


def test_repair_by_vlm_writes_role_repair_block(env):
    e = env()
    write_scenario(e["sc"], default="ok", garbled_pages=[2],
                   rules=[{"match": {"engine": "docling"}, "behavior": "garbled"},
                          {"match": {"engine": "mineru"}, "behavior": "garbled_sticky"}])
    # docling main engine, garbled page 2; docling's own repair (fake, pypdfium) and mineru (sticky) fail -> vlm repairs
    write_scenario(e["sc"], default="ok", garbled_pages=[2],
                   rules=[{"match": {"engine": "docling"}, "behavior": "garbled_sticky"},
                          {"match": {"engine": "mineru"}, "behavior": "garbled_sticky"}])
    tid = e["task"]("broken_tounicode.pdf", engine="docling")
    assert run_task(e["store"], tid, get_engines(e["cfg"]), e["cfg"]) == TaskStatus.done
    sc = json.loads((e["root"] / "out" / "broken_tounicode" / "broken_tounicode.json").read_text(encoding="utf-8"))
    rep = [p for p in sc["quality"]["pages"] if p.get("repaired_by")]
    assert rep and all(p["repaired_by"].startswith("vlm:openai:") for p in rep)
    assert sc["engine"] == "docling" and sc["vlm"]["role"] == "repair" and sc["vlm"]["pages_sent"] == len(rep)
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (schema bump in `store.py` with the migration pattern used for v2–v4; `maintenance.py` calls `prune_vlm_usage` in its periodic sweep). The repair test relies on the fake runner's `garbled_sticky` honouring `fake_engine` and the fake vlm producing clean text for `pages.pdf` — verify `fake_runner._repairing()` treats a vlm request as a repair when `src` is `pages.pdf`.
- [ ] **Step 4: Run** `uv run pytest tests/unit -q` → pass; `uv run pytest tests/reliability -q` → pass (no vlm configured there).
- [ ] **Step 5: Commit** `feat(pipeline): vlm gate (remote switch, page cap, daily budget), usage table v6, sidecar vlm block, options_key`.

### Task 8: Local runtime install + self-check — **execute exactly one of 8a / 8b / 8c per the Task 1 decision**

#### Task 8a (spike path b): `vlm_runtime.py` installs pinned llama.cpp + GGUF; `selfcheck_vlm.py`

**Files:**
- Create: `src/aidoc/vlm_runtime.py`, `src/aidoc/engines/runner/selfcheck_vlm.py`, `tests/unit/test_vlm_runtime.py`
- Modify: `src/aidoc/setup_engines.py` (`plan_commands("vlm")`, GPU rule for vlm), `src/aidoc/paths.py` (`tools_dir()`), `tests/unit/test_setup_engines.py`

**Interfaces:**
- `vlm_runtime.PINNED = {"llamacpp": {"tag": "<from spike>", "windows_cuda13": {"url": ..., "sha256": ...}, "windows_cudart": {...}, "linux_x64": {...}}, "presets": {"paddleocr-vl-1.6": {"gguf": {"url", "sha256", "name"}, "mmproj": {...}, "prompt_style": "paddle_tasks", "image_max_pixels": 1003520}, "qwen2.5-vl-3b": {...,"prompt_style": "generic"}}}` — values from the spike write-up.
- `download(url, dest, sha256, log, *, opener=urllib.request.urlopen)` (streams to `.tmp`, verifies, `os.replace`), `install(config, log, *, opener=...) -> dict` (manifest), `manifest_path()`, `read_manifest()`, `python -m aidoc.vlm_runtime install` entry point (so `setup_engines` can run it as a subprocess and stream its stdout as log lines).
- `selfcheck_vlm.py` (host interpreter; imports `vlm_runner.LocalServer` and `Client`): starts the server from the manifest, parses the startup log for `offloaded (\d+)/(\d+) layers to GPU` and a CUDA device line, sends `samples/scanned_cht_page.png` with the preset's prompt style, prints one JSON line `{"ok", "device", "gpu_offloaded_layers", "total_layers", "sample_chars", "version": <runtime tag>, "preset"}`. `setup_engines.setup("vlm")`: requires `device` contains `CUDA` and `gpu_offloaded_layers == total_layers` (replaces the torch `sm_120` rule for this engine), `sample_chars ≥ 20`.
- `paths.tools_dir()` → `AIDOC_TOOLS` env or `data_dir() / "tools"`.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_vlm_runtime.py
import hashlib, io, json, zipfile
from pathlib import Path
import pytest
from aidoc import vlm_runtime as RT


def _zip(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names: z.writestr(n, b"bin")
    return buf.getvalue()


def _fake_opener(files):
    class _R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def getheader(self, k, d=None): return str(len(self.getvalue())) if k.lower() == "content-length" else d
    def opener(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        return _R(files[url])
    return opener


def test_download_verifies_sha256(tmp_path):
    data = b"hello"
    op = _fake_opener({"http://x/a": data})
    RT.download("http://x/a", tmp_path / "a", hashlib.sha256(data).hexdigest(), log=lambda s: None, opener=op)
    assert (tmp_path / "a").read_bytes() == data
    with pytest.raises(RT.ChecksumError):
        RT.download("http://x/a", tmp_path / "b", "00" * 32, log=lambda s: None, opener=op)
    assert not (tmp_path / "b").exists() and not list(tmp_path.glob("*.tmp"))


def test_install_writes_manifest_and_files(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_TOOLS", str(tmp_path / "tools")); monkeypatch.setenv("AIDOC_MODELS", str(tmp_path / "models"))
    monkeypatch.setenv("AIDOC_ENVS", str(tmp_path / "envs"))
    zip_bytes = _zip(["llama-server.exe", "ggml.dll"]); cud = _zip(["cudart64_13.dll"]); gguf = b"GGUF" + b"0" * 10; mm = b"GGUF" + b"1" * 10
    pinned = {"llamacpp": {"tag": "bTEST", "windows_cuda13": {"url": "http://x/l.zip", "sha256": hashlib.sha256(zip_bytes).hexdigest()},
                           "windows_cudart": {"url": "http://x/c.zip", "sha256": hashlib.sha256(cud).hexdigest()},
                           "linux_x64": {"url": "http://x/l.zip", "sha256": hashlib.sha256(zip_bytes).hexdigest()}},
              "presets": {"paddleocr-vl-1.6": {"gguf": {"url": "http://x/m.gguf", "sha256": hashlib.sha256(gguf).hexdigest(), "name": "m.gguf"},
                                               "mmproj": {"url": "http://x/p.gguf", "sha256": hashlib.sha256(mm).hexdigest(), "name": "p.gguf"},
                                               "prompt_style": "paddle_tasks", "image_max_pixels": 1003520}}}
    monkeypatch.setattr(RT, "PINNED", pinned)
    op = _fake_opener({"http://x/l.zip": zip_bytes, "http://x/c.zip": cud, "http://x/m.gguf": gguf, "http://x/p.gguf": mm})
    from aidoc.config import AidocConfig
    cfg = AidocConfig(); cfg.vlm.backend = "local"
    logs = []
    man = RT.install(cfg, logs.append, opener=op)
    assert Path(man["files"]["server_exe"]).exists() and Path(man["files"]["gguf"]).exists() and Path(man["files"]["mmproj"]).exists()
    assert man["runtime_tag"] == "bTEST" and man["preset"] == "paddleocr-vl-1.6" and man["prompt_style"] == "paddle_tasks"
    assert json.loads(RT.manifest_path().read_text(encoding="utf-8")) == man
    # second install with files present and matching sha: no download
    calls = []
    RT.install(cfg, logs.append, opener=lambda *a, **k: calls.append(a) or op(*a, **k))
    assert calls == []


def test_pinned_table_is_complete():
    for key in ("tag", "windows_cuda13", "windows_cudart", "linux_x64"):
        assert key in RT.PINNED["llamacpp"]
    for name, p in RT.PINNED["presets"].items():
        assert len(p["gguf"]["sha256"]) == 64 and len(p["mmproj"]["sha256"]) == 64 and p["prompt_style"] in ("paddle_tasks", "generic"), name
```

```python
# tests/unit/test_setup_engines.py (append)
def test_setup_vlm_plan_and_gpu_rule(tmp_path, monkeypatch):
    from aidoc.config import AidocConfig
    from aidoc.setup_engines import plan_commands, setup
    cfg = AidocConfig(); cfg.vlm.backend = "local"
    cmds = plan_commands("vlm", cfg)
    assert cmds[0][1:4] == ["-m", "aidoc.vlm_runtime", "install"] and cmds[-1][-1].endswith("selfcheck_vlm.py")
    results = iter([("", ""), (json.dumps({"ok": True, "device": "NVIDIA GeForce RTX 5070 Ti (CUDA)", "gpu_offloaded_layers": 24,
                                             "total_layers": 24, "sample_chars": 41, "version": "bTEST", "preset": "paddleocr-vl-1.6"}), "")])
    def run(cmd, **kw):
        out, err = next(results); return type("R", (), {"returncode": 0, "stdout": out, "stderr": err})()
    monkeypatch.setenv("AIDOC_ENVS", str(tmp_path / "envs"))
    assert setup("vlm", cfg, lambda s: None, run=run)
    cpu = iter([("", ""), (json.dumps({"ok": True, "device": "CPU", "gpu_offloaded_layers": 0, "total_layers": 24, "sample_chars": 41}), "")])
    def run_cpu(cmd, **kw):
        out, err = next(cpu); return type("R", (), {"returncode": 0, "stdout": out, "stderr": err})()
    assert not setup("vlm", cfg, lambda s: None, run=run_cpu)


def test_setup_vlm_with_backend_none_refuses():
    from aidoc.config import AidocConfig
    from aidoc.setup_engines import setup
    logs = []
    assert not setup("vlm", AidocConfig(), logs.append, run=lambda *a, **k: None) and any("backend" in l for l in logs)
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** with the real pinned values from the spike; zip extraction only of the files the manifest needs (`llama-server*`, `*.dll`/`*.so`), into `tools_dir()/llamacpp/<tag>/`. `setup("vlm")` with `backend == "openai"` runs only the self-check (connection test, Task 10 shares the code). Add `tests/fixtures`-independent `samples/scanned_cht_page.png` to `make_fixtures.py`'s sample copy list if not already present (it exists today as the Docling self-check sample — reuse).
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_vlm_runtime.py tests/unit/test_setup_engines.py -q` → pass. Then on this machine: `uv run aidoc setup vlm` with `backend = "local"` → `.ready` written; paste the self-check JSON into the spike write-up.
- [ ] **Step 5: Commit** `feat(vlm): pinned llama.cpp + PaddleOCR-VL-1.6 GGUF runtime install and GPU self-check`.

#### Task 8b (spike path c): `envs/vlm/` torch env + in-runner transformers loop

Only if Task 1 selected (c). Mirror Task 8a's shape: `envs/vlm/pyproject.toml` (torch cu128 via the index pattern of `envs/mineru`, `transformers>=5`), `VlmEngine.python()` → `paths.venv_python("vlm")`, runner gains `backend == "local_transformers"` that loads the model in-process and serves `handle()` without HTTP (the `Client` is replaced by a local callable with the same `.chat()` signature so Task 4's cleaning/retry code is unchanged), `selfcheck_vlm.py` reports `torch.cuda` + `sm_120` like the other GPU engines. Tests: the same as 8a minus downloads, plus the existing `test_setup_engines` GPU rule. Record in deviations why (b) failed.

#### Task 8c (remote only): no local runtime

Only if Task 1 selected rule 4. `setup("vlm")` with `backend == "local"` logs `local backend is not supported on this platform yet (see docs/superpowers/spikes/2026-10-vlm-ocr.md)` and returns False; Settings hides `local` in the backend select (Task 11 reads `/api/system.engines.vlm.local_supported`). Add a unit test for the message and the API field. Record in deviations.

---

### Task 9: Quality — `vlm_overlap` advisory metric, `vlm_per_page` accepted

**Files:**
- Modify: `src/aidoc/quality.py`, `src/aidoc/pipeline.py` (`check_document` passes `layers` + `engine_name`), `tests/unit/test_quality.py`

**Interfaces:**
- `vlm_overlap(section: str, layer: str) -> float | None`: character 3-gram Jaccard between `squash(section)` and `layer` (already squashed by `pagemap.pdf_text_layers`); None when `len(layer) < 120`.
- `assess(..., vlm_pages: Iterable[int] | None = None, layers=None)`: for pages in `vlm_pages` (pages produced by a VLM: all pages when the main engine is vlm; repaired pages with a `vlm:` label otherwise) that are not broken-font pages, compute `vlm_overlap`; `< VLM_OVERLAP_MIN` → add/extend the page entry with `"advisories": ["vlm_low_overlap"]` and `metrics.vlm_overlap`. **Never** changes `level` or `score` (spec §7). Entries that only carry advisories do not count as flagged/unrepaired.

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_quality.py (append)
from aidoc.quality import assess, vlm_overlap
from aidoc.pagemap import squash


def test_vlm_overlap_jaccard():
    layer = squash("The quick brown fox jumps over the lazy dog near the river bank today " * 3)
    assert vlm_overlap("The quick brown fox jumps over the lazy dog near the river bank today " * 3, layer) > 0.9
    assert vlm_overlap("Completely different text about ships and harbours " * 4, layer) < 0.2
    assert vlm_overlap("x", squash("short")) is None


def test_vlm_low_overlap_is_advisory_only():
    good = "The quick brown fox jumps over the lazy dog near the river bank today. " * 6
    md = "".join(f"<!-- page: {i} -->\n\n{good}\n\n" for i in (1, 2)).replace(good, "Ships and harbours and sails and masts. " * 8, 1)
    layers = [squash(good), squash(good)]
    q = assess(md, _probe(2), 2, page_map_method="vlm_per_page", vlm_pages=[1, 2], layers=layers)
    assert q.level == "ok" and q.pages[0]["page"] == 1 and q.pages[0]["advisories"] == ["vlm_low_overlap"]
    assert q.pages_unrepaired == 0 and q.to_json()["pages_unrepaired"] == 0
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**; `pipeline.check_document` passes `vlm_pages` (all pages when `engine_name == "vlm"`, else the pages whose `repaired_by` starts with `vlm:`) and reuses the `layers` it already computes for the spot check (compute once, pass to both). `QualityResult.pages_unrepaired` ignores advisory-only entries (entry without `reasons`).
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_quality.py tests/unit/test_pipeline*.py -q` → pass. **Step 5: Commit** `feat(quality): vlm_low_overlap advisory (collect-only) for VLM-produced pages`.

---

### Task 10: API — `/system` vlm fields, `/system/vlm/check`, `/system/vlm/usage`, settings masking

**Files:**
- Modify: `src/aidoc/server/api/system.py`, `src/aidoc/server/api/settings.py`, `src/aidoc/server/setup_runner.py` (accept `"vlm:check"` as a job that runs the connection test / self-check without install), `src/aidoc/setup_engines.py` (`check_vlm(config, log) -> bool` shared by `setup("vlm")` for openai and by the API)
- Create: `tests/api/test_system_vlm.py`, `tests/api/test_settings_vlm.py`

**Interfaces:** spec §10.1. `engine_status("vlm", ctx)` adds `backend`, `remote`, `local_endpoint`, `endpoint_host`, `model`, `budget` (`{used_today, requests_today, limit}` for remote, else null), `roles`, `local_supported` (True after 8a/8b, False for 8c). `check_vlm` (openai): optional `GET /models` (warn only) + one request with `samples/vlm_check.png` (generated by `make_fixtures.py`: 300×120 px, text `AIDOC VLM CHECK 繁體中文 2026`) → content must contain `AIDOC` and `繁體` (case/space-insensitive) → writes `envs/vlm/.ready` `{"backend": "openai", "endpoint_host", "model", "checked_at"}`; local: runs `selfcheck_vlm.py` without reinstall.

- [ ] **Step 1: Failing tests**

```python
# tests/api/test_system_vlm.py
import json, time
from tests.fakes.vlm_server import FakeVlmServer


def test_system_lists_vlm_with_backend_none(client):
    v = client.get("/api/system").json()["engines"]["vlm"]
    assert v["backend"] == "none" and v["installed"] is False and v["ready"] is False and v["roles"] == {"fallback": True, "repair": True}


def test_system_vlm_openai_fields_and_budget(client_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("AIDOC_VLM_API_KEY", "k")
    c = client_factory(toml='[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:1234/v1"\nmodel = "m"\nremote_enabled = true\nremote_daily_page_budget = 50\n')
    c.app.state.ctx.store.add_vlm_usage(time.strftime("%Y-%m-%d"), "openai", "127.0.0.1:1234", 7, 7, 100, 50)
    v = c.get("/api/system").json()["engines"]["vlm"]
    assert v["backend"] == "openai" and v["remote"] is True and v["local_endpoint"] is True and v["endpoint_host"] == "127.0.0.1:1234"
    assert v["budget"] == {"used_today": 7, "requests_today": 7, "limit": 50} and v["ready"] is True
    u = c.get("/api/system/vlm/usage?days=7").json()
    assert u["today"]["pages"] == 7 and u["days"][0]["prompt_tokens"] == 100


def test_vlm_check_against_fake_endpoint(client_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("AIDOC_VLM_API_KEY", "sk-test")
    with FakeVlmServer({"default": "ok", "pages": {"1": "AIDOC VLM CHECK 繁體中文 2026"}, "require_key": "sk-test"}) as base:
        c = client_factory(toml=f'[vlm]\nbackend = "openai"\nbase_url = "{base}"\nmodel = "fake-vlm"\nremote_enabled = true\n')
        r = c.post("/api/system/vlm/check"); assert r.status_code == 202
        done = c.wait_event("setup.done", lambda p: p["engine"] == "vlm", timeout=10)
        assert done["ok"] is True
        v = c.get("/api/system").json()["engines"]["vlm"]
        assert v["checked_at"] and v["ready"]
    with FakeVlmServer({"default": "unauthorized"}) as base:
        c = client_factory(toml=f'[vlm]\nbackend = "openai"\nbase_url = "{base}"\nmodel = "fake-vlm"\nremote_enabled = true\n')
        c.post("/api/system/vlm/check")
        done = c.wait_event("setup.done", lambda p: p["engine"] == "vlm", timeout=10)
        assert done["ok"] is False and "401" in done["error"]
```

```python
# tests/api/test_settings_vlm.py
def test_settings_mask_and_validate_vlm(client_factory):
    c = client_factory(toml='[vlm]\nbackend = "openai"\nbase_url = "http://127.0.0.1:1/v1"\nmodel = "m"\napi_key = "sk-secret"\n')
    s = c.get("/api/settings").json()
    assert s["vlm"]["api_key"] == "***" and "sk-secret" not in c.get("/api/settings").text
    assert c.put("/api/settings", json={"vlm": {"api_key": "***", "model": "m2"}}).status_code == 200
    assert c.app.state.ctx.config.vlm.api_key == "sk-secret" and c.app.state.ctx.config.vlm.model == "m2"
    assert c.put("/api/settings", json={"vlm": {"backend": "none", "remote_enabled": True}}).status_code == 422
    assert c.put("/api/settings", json={"vlm": {"base_url": "http://u:p@h/v1"}}).status_code == 422
    assert c.put("/api/settings", json={"vlm": {"max_concurrent": 9}}).status_code == 422
    assert c.put("/api/settings", json={"vlm": {"api_key": "sk-new"}}).status_code == 200
    assert c.app.state.ctx.config.vlm.api_key == "sk-new"
```

- [ ] **Step 2: Run** → FAIL (adapt fixture names to `tests/api/conftest.py`'s actual helpers — if `client_factory`/`wait_event` do not exist, add them to the conftest in this task, mirroring how `test_system.py` boots the app with a temp `aidoc.toml` and reads SSE).
- [ ] **Step 3: Implement.** The check job reuses `SetupRunner` (one background job at a time, `setup.log`/`setup.done` events with `engine: "vlm"`).
- [ ] **Step 4: Run** `uv run pytest tests/api -q` → pass. **Step 5: Commit** `feat(api): vlm engine status, connection check job, usage endpoint, [vlm] settings masking`.

### Task 11: Web — Settings VLM card (backend form, consent, test connection, budget meter)

**Files:**
- Modify: `web/src/api/types.ts` (`EngineName` gains `"vlm"`; `EngineInfo` gains `backend?`, `remote?`, `local_endpoint?`, `endpoint_host?`, `model?`, `budget?`, `roles?`, `local_supported?`; `Settings.vlm`), `web/src/api/queries.ts` (`useVlmCheck`, `useVlmUsage`), `web/src/features/settings/EngineCard.tsx` (`ABOUT.vlm`, renders `VlmBackendForm` when `name === "vlm"`), `web/src/pages/SettingsPage.tsx` (`ENGINES` has four; grid `md:grid-cols-2 xl:grid-cols-4`), `web/src/pages/ConvertPage.tsx` (engine select includes `vlm` when ready; 「本次不使用遠端 VLM」 checkbox when `engines.vlm.remote`)
- Create: `web/src/features/settings/VlmBackendForm.tsx`, `web/src/features/settings/isLoopbackUrl.ts`, `web/src/features/settings/__tests__/isLoopbackUrl.test.ts`, `web/src/features/settings/__tests__/VlmBackendForm.test.tsx`, `web/src/features/settings/__tests__/EngineCard.vlm.test.tsx`

**Interfaces:**
- `isLoopbackUrl(url: string): boolean | null` → true for `127.0.0.1`, `localhost`, `[::1]`, `0.0.0.0`; false for any other host; null when unparsable.
- `VlmBackendForm({ settings, info, onSave })`: backend Select (`none`/`local`/`openai`; `local` disabled with tooltip when `info.local_supported === false`); for `openai`: fields `base_url`, `model`, `api_key` (type=password, placeholder `***` when set, empty string = keep), `request_timeout_s`, `max_concurrent`, `remote_max_pages_per_doc`, `remote_daily_page_budget`; consent `Switch` bound to `remote_enabled`, **disabled until** `base_url` and `model` are non-empty; consent text per spec §4.6 with `TriangleAlert` (red) for non-loopback and `Info` (blue) for loopback; for `local`: preset Select + 「安裝」 button (reuses EngineCard's install) + note 「此引擎不抽取圖片」; role switches 「當作最後一道 fallback」/「用於逐頁修復」; 「測試連線」 button → `POST /api/system/vlm/check`, opens the LogPanel (`setup:vlm`); budget `Meter` (`used_today / limit`) + 30-day `Sparkline` from `useVlmUsage`.
- Saving uses the existing `PUT /api/settings` diff flow (`diffSettings`); `api_key` is sent only when the user typed something.

- [ ] **Step 1: Failing tests**

```ts
// web/src/features/settings/__tests__/isLoopbackUrl.test.ts
import { describe, expect, it } from "vitest";
import { isLoopbackUrl } from "../isLoopbackUrl";

describe("isLoopbackUrl", () => {
  it("recognises loopback hosts", () => {
    expect(isLoopbackUrl("http://127.0.0.1:1234/v1")).toBe(true);
    expect(isLoopbackUrl("http://localhost:11434/v1")).toBe(true);
    expect(isLoopbackUrl("http://[::1]:8080/v1")).toBe(true);
  });
  it("treats tailnet and public hosts as remote", () => {
    expect(isLoopbackUrl("http://100.64.0.7:1234/v1")).toBe(false);
    expect(isLoopbackUrl("https://api.openai.com/v1")).toBe(false);
  });
  it("returns null for garbage", () => {
    expect(isLoopbackUrl("not a url")).toBeNull();
  });
});
```

```tsx
// web/src/features/settings/__tests__/VlmBackendForm.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { VlmBackendForm } from "../VlmBackendForm";

const base = { backend: "openai", remote_enabled: false, base_url: "", model: "", api_key: "", api_key_env: "AIDOC_VLM_API_KEY",
  allow_no_key: false, request_timeout_s: 120, max_concurrent: 2, max_output_tokens: 4096, remote_max_pages_per_doc: 200,
  remote_daily_page_budget: 500, use_as_fallback: true, use_for_repair: true, dpi: 150, max_image_px: 2000,
  local_preset: "paddleocr-vl-1.6", local_dpi: 200, local_max_image_px: 1400, local_ctx_size: 8192, local_gguf: "", local_mmproj: "", local_prompt_style: "" };

describe("VlmBackendForm", () => {
  it("keeps the consent switch disabled until base_url and model are set", () => {
    render(<VlmBackendForm settings={base} info={{ installed: false, ready: false, backend: "openai", local_supported: true }} onSave={vi.fn()} />);
    expect(screen.getByRole("switch", { name: /我了解/ })).toBeDisabled();
  });
  it("shows a red warning for a remote host and an info note for loopback", () => {
    const { rerender } = render(<VlmBackendForm settings={{ ...base, base_url: "https://api.example.com/v1", model: "m" }} info={{ installed: false, ready: false, backend: "openai" }} onSave={vi.fn()} />);
    expect(screen.getByText(/會傳到 api.example.com/)).toHaveAttribute("data-tone", "warn");
    rerender(<VlmBackendForm settings={{ ...base, base_url: "http://127.0.0.1:1234/v1", model: "m" }} info={{ installed: false, ready: false, backend: "openai" }} onSave={vi.fn()} />);
    expect(screen.getByText(/端點在本機/)).toHaveAttribute("data-tone", "info");
  });
  it("never echoes a stored key and only sends a typed one", () => {
    const onSave = vi.fn();
    render(<VlmBackendForm settings={{ ...base, base_url: "http://127.0.0.1:1/v1", model: "m", api_key: "***" }} info={{ installed: false, ready: false, backend: "openai" }} onSave={onSave} />);
    const key = screen.getByLabelText("API key") as HTMLInputElement;
    expect(key.value).toBe("") && expect(key.placeholder).toBe("***");
    fireEvent.click(screen.getByRole("button", { name: "儲存" }));
    expect(onSave.mock.calls[0][0].vlm).not.toHaveProperty("api_key");
  });
  it("hides local when the platform does not support it", () => {
    render(<VlmBackendForm settings={base} info={{ installed: false, ready: false, backend: "openai", local_supported: false }} onSave={vi.fn()} />);
    fireEvent.click(screen.getByRole("combobox", { name: "後端" }));
    expect(screen.getByRole("option", { name: /local/ })).toHaveAttribute("aria-disabled", "true");
  });
});
```

- [ ] **Step 2: Run** `pnpm --dir web test -- settings` → FAIL. **Step 3: Implement** (shadcn `Select`, `Switch`, `Input`; `data-tone` attribute on the consent note for the test; `Meter` + `Sparkline` reuse). Update `EngineCard.vlm.test.tsx`: `engineState()` for vlm with `backend === "none"` → label 「尚未選擇後端」 status `queued`; openai ready → 「就緒（遠端）」.
- [ ] **Step 4: Run** `pnpm --dir web test` and `pnpm --dir web build` → green; `pnpm --dir web lint` if configured.
- [ ] **Step 5: Commit** `feat(web): VLM engine card — backend form, remote consent, connection test, budget meter`.

---

### Task 11b: Web badges/labels + MCP schema + e2e

**Files:**
- Modify: `web/src/features/document/pageWarnings.ts` (+ test), `web/src/features/jobs/buildTimeline.ts` (+ test), `web/src/features/library/badges.ts`/`filters.ts` (+ tests), `web/src/pages/LibraryPage.tsx`, `src/aidoc/mcp/schemas.py` (`engine` Literal gains `"vlm"` in `SearchFilters`/`ListFilters`; `DocInfoOut.engine` unchanged type `str`), `tests/mcp/test_tools_search_list.py`
- Create: `web/e2e/vlm.spec.ts`

**Interfaces:** `pageWarnings`: `repaired_by` starting with `vlm:` → 「第 N 頁：文字層損壞，已用 VLM OCR 修復（<model>）」 (model = text after the second colon, or the preset); `buildTimeline`: attempt rows for `vlm` show a subtitle `vlm · <backend> · <model|preset>` from the task's `tried[*]`/sidecar `vlm`; Library engine filter options include `vlm`; card shows 「遠端」 small text when `document.vlm?.backend === "openai" && !local_endpoint` (the API's `serialize_document` exposes the sidecar `vlm` block — add to `documents.py` if missing).

- [ ] **Step 1: Failing tests**

```ts
// web/src/features/document/__tests__/pageWarnings.vlm.test.ts
import { describe, expect, it } from "vitest";
import { pageWarnings } from "../pageWarnings";

describe("pageWarnings with VLM repairs", () => {
  it("names the VLM model", () => {
    const w = pageWarnings({ score: 1, level: "ok", reasons: [], pages: [{ page: 61, reasons: ["broken_text_layer"], repaired_by: "vlm:openai:qwen2.5-vl-7b" }] } as any);
    expect(w.get(61)).toEqual({ text: "第 61 頁：文字層損壞，已用 VLM OCR 修復（qwen2.5-vl-7b）", tone: "info" });
  });
  it("uses the preset for the local backend", () => {
    const w = pageWarnings({ score: 1, level: "ok", reasons: [], pages: [{ page: 1, reasons: ["garbage"], repaired_by: "vlm:local:paddleocr-vl-1.6" }] } as any);
    expect(w.get(1)?.text).toContain("VLM OCR 修復（paddleocr-vl-1.6）");
  });
});
```

```python
# tests/mcp/test_tools_search_list.py (append)
async def test_list_documents_accepts_engine_vlm(mcp_client):
    out = await mcp_client.call_tool("list_documents", {"filters": {"engine": "vlm"}})
    assert not out.isError
```

```ts
// web/e2e/vlm.spec.ts — outline (the e2e harness starts the server with AIDOC_FAKE_ENGINES=1 and a scenario; add a
// fake VLM endpoint via `tests/fakes/vlm_server.py` started by the Playwright global setup, base_url written to env)
// 1. Settings → vlm card → backend "openai" → base_url = fake, model = "fake-vlm", key "sk-test" → consent switch → 儲存
// 2. 「測試連線」 → log panel shows "ok"; card badge 就緒（遠端）
// 3. Convert a fixture whose scenario makes docling/mineru/markitdown "low" → Jobs timeline shows attempts
//    docling, mineru, markitdown, vlm · openai · fake-vlm → Library card engine "vlm" with 「遠端」 text.
```

- [ ] **Step 2–4:** run → fail → implement → `pnpm --dir web test`, `uv run pytest tests/mcp -q`, `pnpm --dir web e2e` green.
- [ ] **Step 5: Commit** `feat(web,mcp): VLM labels in page warnings, timeline, library filter; engine literal; e2e`.

---

### Task 12: Reliability suite — fake engines + fake endpoint end to end

**Files:**
- Create: `tests/reliability/test_vlm.py`

Scenarios (each a test; all with `AIDOC_FAKE_ENGINES=1`, `[vlm] backend = "openai"` pointing at a `FakeVlmServer`, key via env; the fake runner's `vlm` engine is used for conversion, the fake **endpoint** is used by the real `vlm_runner.py` only in the two tests marked ⚑ which set `AIDOC_FAKE_ENGINES_EXCEPT=vlm` — add that env to `registry.get_engines()`: fake all engines except the listed ones):

1. **fallthrough**: three fakes `low` → `vlm` `done`; `tried` has four entries; sidecar `vlm.role == "primary"`.
2. **repair by vlm**: Docling main, `garbled_sticky` on docling+mineru → `repaired_by` starts with `vlm:`; `pages_unrepaired == 0`.
3. **remote disabled → zero requests** ⚑: `remote_enabled = false` with everything else configured; same scenario as 2 → page stays `warn`; the fake endpoint's `calls.jsonl` **does not exist or is empty**.
4. **budget**: `remote_daily_page_budget = 5`, 10-page `big.pdf`-like fixture (`span_margin.pdf` 45 pages → cap with `remote_max_pages_per_doc = 10` instead) → `missing` log `vlm(page_cap)`; then repair of 3 pages with budget 5 → allowed; another 3 → `repair: vlm skipped (budget)`.
5. **hang → timeout → cancel** ⚑: endpoint `hang`, `request_timeout_s = 2`, `--timeout 5`; assert the task ends `low`/`failed` within 60 s with `transient` attempts logged, and that a `cancel` set during the hang yields `cancelled` with no `vlm_runner.py` process alive (psutil scan by cmdline).
6. **refuse/length → warn** ⚑: endpoint rules `refuse` page 1, `length` page 2 on `text.pdf` forced `--engine vlm` → `page_conversion_failed` entries, level `warn`, sidecar `vlm.refused_pages == [1]`, `truncated_pages == [2]`.
7. **resume after kill with model change**: VLM main engine, 45-page `big.pdf` (two segments), kill the process after segment 0 is done (reuse `test_recovery` helpers), change `vlm.model`, re-run → log contains `options changed since the done segments were made` and both segments are reconverted.
8. **key hygiene** ⚑: after test 6, grep every file under the task's `data/work/<task>/` and the output dir and the collected log lines for `sk-test` → none.

- [ ] **Step 1:** write the eight tests (follow `tests/reliability/test_page_quality.py` conventions and its fixtures). **Step 2:** run → fail on missing `AIDOC_FAKE_ENGINES_EXCEPT`. **Step 3:** implement the registry env + any pipeline gap found. **Step 4:** `uv run pytest tests/reliability -q` green. **Step 5: Commit** `test(reliability): vlm fallthrough, repair, privacy, budget, timeout/cancel, refusal, resume, key hygiene`.

---

### Task 13: Slow real tests, docs, acceptance record

**Files:**
- Create: `tests/integration/test_engine_vlm.py`, `tests/integration/test_vlm_repair_real.py`
- Modify: `README.md` (Engines table row `VLM OCR`; Settings/privacy paragraph; `aidoc setup vlm`; "does not extract images"), `skill/SKILL.md` (mention `repaired_by` `vlm:` and `--no-vlm-remote`), spec §15 acceptance record (new section), this file's "Implementation notes / deviations", `docs/superpowers/spikes/2026-10-vlm-ocr.md` (final numbers)

Slow tests (spec §11.5; `require_engine("vlm")` through `tests/manual.py`; when Task 8c was chosen, `require_engine("vlm")` skips/fails on the missing `.ready` as for any engine and the deviations section says so):

```python
# tests/integration/test_engine_vlm.py (outline)
@pytest.mark.slow
@pytest.mark.parametrize("name,expected", [("scanned_cht.pdf", ZH), ("scanned_mixed.pdf", ZH + EN), ("page.png", ZH + EN)])
def test_vlm_recall(name, expected, tmp_path): ...   # --engine vlm, recall >= 0.95, level in (ok, warn)

@pytest.mark.slow
def test_vlm_formula_and_table(tmp_path): ...        # formula.pdf contains "$" or the spike-approved fallback; text.pdf p2 table per spike

# tests/integration/test_vlm_repair_real.py
@pytest.mark.slow
def test_cover_repaired_by_vlm_when_a_and_b_are_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("AIDOC_REPAIR_ONLY", "vlm")      # repair.repair_order honours it (test hook, documented in repair.py)
    ...                                                 # hfad_cover_p1.pdf, docling main → contains "Wouldn't it be dreamy", repaired_by startswith "vlm:"

@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("AIDOC_VLM_REAL_BASE_URL"), reason="no real remote endpoint configured")
def test_remote_endpoint_recall(tmp_path): ...         # recall >= 0.90 on scanned_cht.pdf
```

- [ ] **Step 1:** write the tests; `AIDOC_REPAIR_ONLY` hook in `repair.repair_order` (comma list of engine names to keep; test-only, documented).
- [ ] **Step 2:** `AIDOC_REQUIRE_MANUAL=1 uv run pytest -m slow tests/integration/test_engine_vlm.py tests/integration/test_vlm_repair_real.py -q` on the GPU host → green (or, for 8c, the local tests fail on `require_engine` in acceptance mode — record that the local backend is not delivered and run the remote test against a local LM Studio/Ollama endpoint instead, with numbers).
- [ ] **Step 3:** docs: README, skill, spec acceptance record (seconds/page, peak VRAM, tokens/page, recall table), deviations section below.
- [ ] **Step 4:** full regression: `uv run pytest -q`, `pnpm --dir web test`, `pnpm --dir web build`, `pnpm --dir web e2e`, `AIDOC_REQUIRE_MANUAL=1 uv run pytest -m slow -q`.
- [ ] **Step 5: Commit** `docs: VLM engine acceptance record, README/skill updates` and push.

---

## Coverage table (spec section → tasks)

| Spec § | Content | Tasks |
|---|---|---|
| §1.2 goals 1–6 | engine name, two backends, routing/repair roles, privacy, quality, tests | 0, 4, 4b, 5, 6, 7, 9, 12 |
| §2 candidates & licences | Marker/Chandra excluded; PaddleOCR-VL chosen | 1 (write-up cites), 13 (README) |
| §3.1–§3.3 verification | facts recorded; spike confirms | 1, 2 |
| §3.4 spike S1 decision rule | | 1 → selects 8a/8b/8c |
| §4.1 one engine, two backends, `available()` | | 5 |
| §4.2 runner protocol, `result.json.vlm`, `RawResult.extra` | | 0, 4, 7 |
| §4.3 local backend lifecycle, OOM mapping, install | | 4b, 8a (8b/8c) |
| §4.4 remote: prompt, rendering, retries, truncation, refusal, usage | | 4, 3 (fake) |
| §4.5 images missing | | 4 |
| §4.6 privacy: switch, loopback, log line, sidecar host, budget, cap, key handling | | 0, 5, 7, 10, 11, 12 |
| §4.7 failure classes | | 4, 4b, 7, 12 |
| §5 routing table, `last` semantics | | 5, 7 |
| §6 repair order + VLM acceptance | | 6, 7, 12 |
| §7 quality interactions, `vlm_low_overlap` | | 9 |
| §8.1 sidecar `vlm` block | | 7 |
| §8.2 `vlm_usage` schema v6 | | 7 |
| §8.3 `[vlm]` config + validation + `options_key` | | 0, 7, 10 |
| §9 setup + self-check | | 8a/8b/8c, 10 |
| §10.1 API | | 10 |
| §10.2 web | | 11, 11b |
| §11.1 fake endpoint | | 3 |
| §11.2 unit | | 0, 4, 4b, 5, 6, 7, 8, 9 |
| §11.3 reliability | | 12 |
| §11.4 API/web/e2e | | 10, 11, 11b |
| §11.5 slow | | 13 |
| §13 acceptance | | 13 |
| §14 spec/index amendments | | 0, 13 |

## Phase acceptance

1. `backend = "none"`: `uv run pytest -q` green; `test_vlm_absent_changes_nothing` passes; Settings shows the fourth card as 「尚未選擇後端」.
2. Fake-endpoint suites green: `tests/unit/test_vlm_runner.py`, `test_vlm_local_server.py`, `tests/api/test_system_vlm.py`, `tests/reliability/test_vlm.py` (privacy test 3 asserts zero calls; key-hygiene test 8 finds no key).
3. Spike write-up committed with numbers and an explicit decision; exactly one of Tasks 8a/8b/8c executed and named in the deviations section.
4. GPU host: `aidoc setup vlm` reproducible from a clean `data/tools` + `data/models/vlm` (sha256 verified); slow tests per spec §11.5 green (or 8c recorded); no `llama-server` process remains after cancelling a conversion (checked with `tasklist`/psutil in the acceptance record).
5. Remote path exercised once against a real local endpoint (LM Studio or Ollama) with model, seconds/page and tokens/page recorded in the acceptance record.
6. `pnpm --dir web test`, `pnpm --dir web build`, `pnpm --dir web e2e` green.
7. README, skill, index amendments committed; spec acceptance record filled.

## Implementation notes / deviations

_(filled in by the implementer: every departure from the spec or this plan, with the pinning test; the Task 1 decision and the Task 8 variant executed; measured numbers; anything not done and why.)_
