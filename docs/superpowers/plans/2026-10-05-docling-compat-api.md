# docling-serve Compatible API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Read `docs/superpowers/plans/2026-09-24-index.md` (cross-phase contracts, §0 constraints, §8 API conventions) and `docs/superpowers/plans/2026-10-01-mcp-server.md` (§0, §3, §4.1, §4.2: token model, gate, call log, store surface) before any task. Task 1 is a spike whose rulings bind later tasks.

**Goal:** Any client that speaks the docling-serve v1 HTTP API (Open WebUI's Docling extraction engine, RAGFlow's Docling parser, Haystack, n8n, curl) can point at Doc4AI Studio and get Markdown back from the orchestrated pipeline (routing + per-page quality + repair), with page numbers that survive into the client, authenticated by the same personal access tokens as MCP and visible in the same admin call log.

**Spec:** `docs/superpowers/specs/2026-10-05-docling-compat-api-design.md` (Traditional Chinese; §3 holds the verified client request shapes every test below replays; §4 the endpoint contract; §10 the error table).

**Architecture:** A new package `src/aidoc/compat/` adds a FastAPI router mounted at the root (`/v1/*`, `/v1alpha/convert/source`, `/health`) before the SPA catch-all and outside the `/api` token dependency. Its auth dependency reads `X-Api-Key` or `Authorization: Bearer`, runs the existing `PatVerifier` / `AuthFailureGuard` / `RateLimiter` from `ctx.extras["mcp"]`, and records every request through the existing `CallRecorder` as `method = "compat/docling"`. Conversions go through `tools_convert.reserve_job` / `start_job_for_*` / `wait_for_task` (one pipeline, one job cap, one upload area); sync requests wait for a job slot instead of failing; async requests get `task_id = job_id` and a new `compat_tasks` table remembers the options for `/v1/result`. Responses are assembled from `<stem>.md` by `pagemap.split_pages()` with docling's `md_page_break_placeholder` semantics (placeholder between pages, empty segments for missing pages) plus a `document.doc4ai` block.

**Tech Stack:** Python 3.10+ (FastAPI, Starlette multipart streaming, pydantic v2), SQLite schema v5, pytest + `httpx`, `jsonschema` (already a transitive dependency of `mcp`) for the OpenAPI snapshot test; React/TypeScript for the three small UI touches (Jobs origin badge, token dialog text, Overview line).

---

## 0. Global constraints

Index §0 and MCP plan §0 apply. In addition:

- `/v1/*`, `/v1alpha/*`, `/health` are **not** under `/api`: no `workspace` envelope (`EnvelopeMiddleware` passthrough list gains `/v1/`, `/v1alpha/`, `/health`), no `auth.require_token`. Error bodies are `{"detail": ...}` (docling-serve / FastAPI style, spec §10), never `{"error": ...}`.
- The token is read from `X-Api-Key` first, then `Authorization: Bearer`; both present and different → 401; `?api_key=` / `?token=` → 400 logged `auth_error/token_in_query`. PATs stay invalid on `/api/*`; the server token stays invalid on `/v1/*`.
- One conversion pipeline: never call `pipeline.run_task` directly; always `tools_convert.reserve_job` (or the new waiting variant) → `start_job_for_*` → `wait_for_task`. Jobs get `origin = "compat"`.
- Every compat request writes exactly one `mcp_calls` row (`method = "compat/docling"`, `protocol_version = "docling-serve-v1"` or `-v1alpha`) and never the token or file content (`args_summary` ≤ 500 chars: filename, size, sha256[:8], to_formats, placeholder flag, lang, engine).
- `md_content` page breaks: placeholder **between** pages, none before page 1, empty segments for pages without a marker, markers removed when a placeholder is given (kept with `doc4ai_keep_page_markers`); without a placeholder the `<!-- page: N -->` markers stay.
- Response `status` values come only from docling's `ConversionStatus` enum: `success | partial_success | failure | pending | started | skipped`. A failed conversion is HTTP 200 + `status: "failure"` + `errors[]`, not a 4xx.
- Schema **v5**: only the new table `compat_tasks`. Config section `[compat]` exactly as spec §7.
- Concurrency note: `src/aidoc/server/app.py`, `src/aidoc/config.py`, `aidoc.toml`, `src/aidoc/server/api/settings.py` are being edited by other agents (Office preview, VLM OCR). Before editing any of them: `git pull --rebase`, re-read the file, keep edits to the lines named in the task, and never `git add -A` — add the files you changed by name.
- Commit trailer (both lines at the end of every commit message made while executing this plan):
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0175gLq9uam6M5Z9yNNxM4Ba
  ```

## 1. Review focus (input classes the spec implies but no example exercises)

1. **A multipart upload whose declared `Content-Length` is small but whose body is huge** (or absent): the file is streamed to the part path with a running byte count; crossing `max_file_mb` aborts the read, deletes the part and answers 413 — never a MemoryError. → Task 7 `test_oversized_stream_is_cut_and_part_deleted`.
2. **A placeholder that already occurs in the document text** (e.g. `\f` inside a code block, or a placeholder of `---`): we insert exactly `pages - 1` placeholders and document that occurrences inside the text are the client's problem (docling-serve behaves the same); the test asserts the count we insert, not the total count. → Task 6 `test_placeholder_count_is_pages_minus_one_even_if_text_contains_it`.
3. **A document whose page map is incomplete** (`coverage < 1`): placeholder mode emits empty segments so the k-th segment is still page k+1; `doc4ai.page_map.coverage` says why some are empty. → Task 6 `test_missing_pages_become_empty_segments`.
4. **The same file submitted twice concurrently** (two Open WebUI workers): the second sync request must not create a second job — it finds `busy_task_id` and waits on that task. → Task 7 `test_concurrent_same_file_waits_on_the_running_task`.
5. **A revoked token during a long sync wait**: the request was authenticated at the start; it completes (the job is already running) but the log row carries the token id and the next request is 401. → Task 7 `test_revocation_mid_wait_does_not_abort_the_running_job`.

## 2. File structure

```
aidoc.toml                                 T2  + [compat]
src/aidoc/config.py                        T2  + Compat dataclass, _SECTIONS["compat"]
src/aidoc/server/api/settings.py           T2  + [compat] ranges
src/aidoc/server/app.py                    T2  /health; T4 router mount before _mount_web; Envelope passthrough
src/aidoc/store.py                         T3  SCHEMA_VERSION = 5, compat_tasks + methods
src/aidoc/server/maintenance.py            T3  prune_compat_tasks
src/aidoc/compat/__init__.py               T4  package marker
src/aidoc/compat/auth.py                   T4  compat_principal() dependency, CompatCall (log helper), errors
src/aidoc/compat/options.py                T5  CompatOptions, parse_form_options(), parse_json_options(), LANG_MAP
src/aidoc/compat/render.py                 T6  assemble_markdown(), image modes, text_content, doc4ai_block(), ConvertDocumentResponse models
src/aidoc/compat/router.py                 T7  sync endpoints (+ v1alpha), T8 async endpoints, T9 assets, capabilities
src/aidoc/compat/jobs.py                   T7  reserve_job_waiting(), start_job_for_file(), task_to_docling_status()
src/aidoc/mcp/tools_convert.py             T7  start_job_for_file() extracted; start_job_for_bytes() calls it
src/aidoc/mcp/calllog.py                   T10 ua_identity() (User-Agent → client name/version)
src/aidoc/mcp/snippets.py                  T10 + "openwebui" snippet (env block)
src/aidoc/server/api/mcp.py                T10 status: compat_url, compat_calls_24h
web/src/api/types.ts                       T10 Job.origin "compat"; McpStatus.compat_*
web/src/features/jobs/...                  T10 origin badge label 「相容 API」
web/src/features/mcp/OverviewTab.tsx       T10 compat line
web/src/features/mcp/CreateTokenDialog.tsx T10 scope hint text
tests/compat/__init__.py, conftest.py      T4  fixtures: compat_env (token issuer + app), live compat server
tests/compat/test_config.py                T2
tests/compat/test_store_v5.py              T3
tests/compat/test_auth.py                  T4
tests/compat/test_options.py               T5
tests/compat/test_render.py                T6
tests/compat/test_sync.py                  T7
tests/compat/test_async.py                 T8
tests/compat/test_assets.py                T9
tests/compat/test_logging_admin.py         T10
tests/compat/shapes/*.json                 T11 replayed client request shapes
tests/compat/fixtures/docling_serve_openapi_1_36_0.json  T11 snapshot
tests/compat/test_shapes.py                T11
docs/integrations/{open-webui,ragflow,kotaemon,dify,curl}.md  T12
docs/integrations/kotaemon_doc4ai_loader.py                   T12 reference loader
README.md                                  T12 "RAG platforms" section
docs/superpowers/spikes/2026-10-compat.md  T1  spike record
```

## 3. Binding decisions

- **D1 Auth as a FastAPI dependency, not an ASGI gate.** `/v1` endpoints are ordinary FastAPI routes; `Depends(compat_principal)` returns a `Principal` or raises `HTTPException(401/403/400/429)` with the docling-style `detail` body and the `WWW-Authenticate` header built by `aidoc.mcp.gate.www_authenticate("invalid_token", text)` (no `resource_metadata`; Spec 4 will pass it when OAuth is on). The dependency also records auth failures through `CallRecorder.record(status="auth_error", …)` exactly like `McpGate`.
- **D2 Logging via a request-scoped `CompatCall` object** (created by the dependency, stored on `request.state.compat`): endpoints set `tool_name`, `job_id`, `args`, `status`, `error_code`; a `finally` in a tiny router-level middleware (`@router.middleware`-style wrapper installed in `create_app`) writes the single row with `duration_ms` / `response_bytes` / `http_status`. Auth failures are written by the dependency itself (the wrapper sees `request.state.compat is None` and skips).
- **D3 Sync requests wait for a job slot**: `compat.jobs.reserve_job_waiting(ctx, opts, token_id, deadline)` loops `Store.create_mcp_job(opts, token_id=…, limit=cfg.compat.max_concurrent_jobs_per_token)` every 0.5 s until it returns an id or the deadline passes (then 504 `sync_timeout` *before* a job exists). Async requests call it once and answer 429 + `Retry-After: 5` when full.
- **D4 Same file already converting** (`Store.busy_task_id(sha, out_dir)`): sync waits on that task (no new job, `doc4ai.cached: false, attached: true`); async answers that task's `job_id` as `task_id`.
- **D5 `task_id = job_id`.** `/v1/status/poll` and `/v1/result` resolve `compat_tasks(job_id)` → `token_id` must equal the principal's (else 404) → `store.list_tasks(job_id)[0]` is the one task.
- **D6 Status mapping** (`compat.jobs.task_to_docling_status`): `queued → pending`; `probing|converting|checking → started`; `done|skipped → success`; `low → partial_success`; `failed|cancelled → failure`. `skipped` tasks (cache hit at pipeline level) read the cached document.
- **D7 `json_content` is `null`**, `unsupported_formats` lists `json`/`html`/… — unless spike S1 shows RAGFlow breaks, in which case Task 6 adds a minimal `json_content = {"schema_name": "doc4ai", "pages": [{"page": n, "markdown": …}]}` behind `compat.json_content = "null" | "doc4ai"` (default per the spike).
- **D7a (spike S1, 2026-10-10):** RAGFlow 1.0.0-rc1 reads `md_content` first, so D7 stays (`json_content: null`, default `"null"`). Its Docling parser uses a strict SSRF HTTP client that refuses private / loopback / CGNAT (Tailscale) hosts, so the RAGFlow recipe needs a globally routable Doc4AI URL.
- **D7b (spike S2, 2026-10-10):** docling's enums bind our shapes: `ErrorItem.component_type` ∈ `pipeline|user_input|…`, `category` ∈ `FailureCategory` (no `"quality"`: A2's note uses `category: "unknown"`, `module_name: "doc4ai.quality"`); `task_meta` is `TaskProcessingMeta {num_docs, num_processed, num_succeeded, num_partially_succeeded, num_failed}` (+ extra `pages_done`, `pages_total`); `TaskFailureResult.failure` and `TaskStatusResponse.failure` are `PublicFailureInfo {category, message, retryable, phase, details}`.
- **D8 Image handling** uses `aidoc.mcp.docs.rewrite_assets()`'s regexes (`_ASSET_MD`, `_ASSET_HTML`), exposed as `docs.asset_refs(md)`; `referenced` mode rewrites to `{scheme}://{host}/v1/doc4ai/documents/{doc_id}/assets/{name}` using the request's `url.scheme`/`headers["host"]` (behind a trusted proxy Spec 4's `public_url` wins — hook: `compat.render.asset_base_url(request, ctx)`).
- **D9 User-Agent identity**: `calllog.ua_identity(ua) -> (name, version)` takes the first product token (`python-requests/2.32.3` → `("python-requests", "2.32.3")`, `Go-http-client/1.1`, `curl/8.9.1`); empty → `("unknown", None)`. `CallRecorder.note_client()` is reused with `protocol_version = "docling-serve-v1"`.
- **D10 Options parsing is lenient**: unknown keys → `ignored_options`; wrong types (e.g. `to_formats` not a list) → 422 with FastAPI's `detail` list shape; multipart list fields accept repeated fields and comma-separated values; booleans accept `true/false/1/0/yes/no`.

## 4. Contract amendments to `2026-09-24-index.md` (additive)

- **§4 schema v5**: `compat_tasks(job_id TEXT PK, token_id TEXT NOT NULL, options_json TEXT NOT NULL, created_at REAL NOT NULL, protocol TEXT NOT NULL)`; Store: `create_compat_task(job_id, token_id, options: dict, protocol)`, `get_compat_task(job_id) -> dict | None` (row exposes `task["options"]`), `prune_compat_tasks(older_than_ts) -> int`.
- **§7 config**: `[compat]` keys per spec §7 (`enabled`, `sync_timeout_s` 10–3600, `max_file_mb` 1–4096, `max_source_mb` 1–1024, `embed_images_max_mb` 1–1024, `max_concurrent_jobs_per_token` 1–100, `poll_max_wait_s` 1–300, `task_retention_days` 1–3650, `json_content` enum `null|doc4ai`).
- **§8 API**: `Job.origin` may be `"compat"`; `GET /api/mcp/status` gains `compat: {enabled, base_url, calls_24h}`; `GET /api/mcp/config-snippets` gains a snippet `client: "openwebui"`; `mcp_calls.method` may be `compat/docling`.
- New public (non-`/api`) routes per spec §4.1. `GET /health` → `{"status": "ok"}` (also used by Spec 4's Docker HEALTHCHECK).

## 5. Spec ambiguities resolved here

| # | Ambiguity | Resolution |
|---|---|---|
| A1 | Spec §4.3 step 6 says 504 "carries task_id" but the job may not exist yet when the wait-for-slot times out. | 504 body has `task_id: null` when no job was created (`detail: "no job slot became free"`), else the job id. |
| A2 | `partial_success` for `low`: Open WebUI treats anything but `failure|skipped` as success. | Keep `partial_success` (honest) and put the reason list in `errors[]` with `module_name: "doc4ai.quality"`, `category: "unknown"` (spike S2: `"quality"` is not a docling `FailureCategory`) so clients that surface `errors` show it; `status` stays non-failure. |
| A3 | `text_content` when `to_formats` has only `text`: is `md_content` still filled? | Yes — docling-serve fills only requested formats, but Open WebUI reads `md_content` unconditionally; we always fill `md_content`. |
| A4 | `/v1/capabilities` is unauthenticated in docling-serve. | Unauthenticated here too, but static: engine readiness is included only as booleans. |
| A5 | Where the compat router's Origin check lives. | In `compat_principal`: an `Origin` header that is not the request's `Host` → 403 `cross-origin request` (same rule as `McpGate.same_origin`). |

## 6. Coverage table (spec section → tasks)

| Spec | Requirement | Task(s) |
|---|---|---|
| §1.4 (1) Open WebUI end to end | | T12, T13 (manual) |
| §1.4 (2) OpenAPI-driven compat tests | | T11 |
| §1.4 (3) 300-page sync/async | | T7, T8, T13 |
| §1.4 (4) auth + log + isolation | | T4, T10 |
| §1.4 (5) marker count = pages | | T6 |
| §3.1–3.6 verified shapes | replay | T11 shapes |
| §4.1 table | every route, auth, scope | T2 (`/health`), T7, T8, T9 |
| §4.2 request parsing + options table | | T5, T7 |
| §4.3 sync flow 1–7 | | T7 |
| §4.4 markdown assembly, doc4ai block | | T6 |
| §4.5 async flow | | T8 |
| §5 auth | | T4 |
| §6 logging, clients, jobs origin, admin | | T10 |
| §7 config | | T2 |
| §8 recipes | | T12 |
| §9.1 shapes, §9.2 unit, §9.3 slow, §9.4 manual | | T11, T2–T10, T13 |
| §10 error table | | T4, T7, T8 |
| §11 U1/U2 | spike | T1 |

---

## Conventions for every task in this file

- Run tests with `uv run pytest <paths> -q` (fast suite; fake engines via the `ctx` fixture). The whole suite (`uv run pytest -m "not slow" -q`) and `ruff check src tests` must be green before each commit.
- Test fixtures come from `tests/conftest.py` (`ctx`, `client`, `live_server`, `tmp_root`) and `tests/mcp/conftest.py` (`make_doc`, `page_md`, `TINY_PNG`); `tests/compat/conftest.py` (Task 4) adds `compat_env` and `compat_client`.
- Commit after each task with the trailer in §0.

### Task 1: SPIKE — RAGFlow response handling, docling model tolerance, OpenAPI snapshot

**Files:**
- Create: `docs/superpowers/spikes/2026-10-compat.md`
- Create: `tests/compat/fixtures/docling_serve_openapi_1_36_0.json` (downloaded snapshot)

**Questions (each gets Finding / Decision / Affects):**

- **S1 RAGFlow and `json_content: null`.** Read `internal/parser/parser/pdf_parser_docling.go` at the RAGFlow tag matching 1.0.0-rc1 (`gh api repos/infiniflow/ragflow/contents/internal/parser/parser/pdf_parser_docling.go?ref=<tag>`). Record: (a) does it fall back to `md_content` when `json_content` is null/absent; (b) what it does with a response that has no chunks although `do_chunking` was sent; (c) which status values it accepts. **Decision rule:** if (a) is "uses md_content" → D7 stays (`json_content: null`); otherwise set `[compat] json_content = "doc4ai"` as default and Task 6 implements the minimal JSON.
- **S2 Pydantic tolerance.** `uv run --with docling==<current> python -c "from docling.datamodel.service.responses import ConvertDocumentResponse; ConvertDocumentResponse.model_validate({...our sample with doc4ai keys...})"` — does `model_config` ignore extra keys (default) or forbid them? **Decision rule:** ignore → keep extra keys inside `document.doc4ai` and top-level `doc4ai`; forbid → move all extras under a single top-level key and set `document` strictly to the docling fields.
- **S3 OpenAPI snapshot.** `docker run --rm -p 5001:5001 ghcr.io/docling-project/docling-serve-cpu:v1.36.0` (or `uvx docling-serve run`) → `curl :5001/openapi.json > tests/compat/fixtures/docling_serve_openapi_1_36_0.json`; note the schema names `ConvertDocumentResponse`, `TaskStatusResponse`, `ConvertSourcesRequest`, `ConversionStatus`. If no container runtime is available on the host, fetch the file from a published build (document the URL) — the test in Task 11 must not depend on a running docling-serve.
- **S4 Starlette multipart streaming.** Confirm `await request.form()` buffers files to `SpooledTemporaryFile` (size unbounded) — if so Task 7 must read the raw body stream with `python-multipart`'s streaming parser or enforce `Content-Length` first. Record the chosen approach (expected: `starlette.requests.Request.stream()` + `multipart.MultipartParser` callbacks writing to the part path with a byte cap).
- **S5 Open WebUI parallelism.** Read `backend/open_webui/routers/retrieval.py` / `files.py` for how many uploads are processed concurrently (thread pool size). Record; it only tunes the default of `max_concurrent_jobs_per_token` (keep 8 unless the pool is larger).

- [ ] **Step 1:** Run the probes; write the spike file with one `### S<n>` block each.
- [ ] **Step 2:** If S1/S2 change a binding decision, edit §3 D7/D8 of this file **in the same commit** and note it in the spike.
- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/spikes/2026-10-compat.md tests/compat/fixtures/docling_serve_openapi_1_36_0.json docs/superpowers/plans/2026-10-05-docling-compat-api.md
git commit -m "docs(spike): docling-serve compat API — RAGFlow handling, model tolerance, OpenAPI snapshot

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0175gLq9uam6M5Z9yNNxM4Ba"
```

---

### Task 2: `[compat]` config, settings whitelist, `GET /health`

**Files:**
- Modify: `src/aidoc/config.py` (add `Compat` dataclass after `Mcp`/`Preview`, register in `_SECTIONS`, field on `AidocConfig`)
- Modify: `aidoc.toml` (append `[compat]` with spec §7 defaults and comments)
- Modify: `src/aidoc/server/api/settings.py` (`_MIN`/`_MAX` entries, `json_content` enum)
- Modify: `src/aidoc/server/app.py` (`/health` route before `_mount_web`; Envelope passthrough for `/v1/`, `/v1alpha/`, `/health`)
- Test: `tests/compat/test_config.py`, `tests/api/test_system_settings.py` (append)

**Interfaces:**
- `AidocConfig.compat: Compat` with `enabled=True, sync_timeout_s=600, max_file_mb=200, max_source_mb=50, embed_images_max_mb=20, max_concurrent_jobs_per_token=8, poll_max_wait_s=60, task_retention_days=30, json_content="null"`.
- `GET /health` → `200 {"status": "ok"}` with no `workspace` key, no auth, `Cache-Control: no-store`.

- [ ] **Step 1: Write the failing tests**

`tests/compat/__init__.py` (empty) and `tests/compat/test_config.py`:

```python
from pathlib import Path


def test_compat_defaults_and_load(tmp_path):
    from aidoc.config import load_config
    p = tmp_path / "aidoc.toml"
    p.write_text('[compat]\nenabled = false\nsync_timeout_s = 120\njson_content = "doc4ai"\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.compat.enabled is False and cfg.compat.sync_timeout_s == 120 and cfg.compat.json_content == "doc4ai"
    d = cfg.compat
    assert (d.max_file_mb, d.max_source_mb, d.embed_images_max_mb) == (200, 50, 20)
    assert (d.max_concurrent_jobs_per_token, d.poll_max_wait_s, d.task_retention_days) == (8, 60, 30)
    assert "compat" in cfg.to_dict()


def test_committed_toml_has_compat():
    from aidoc.config import load_config
    cfg = load_config(Path(__file__).resolve().parents[2] / "aidoc.toml")
    assert cfg.compat.enabled is True and cfg.compat.json_content == "null"


def test_health_is_public_and_not_enveloped(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert r.headers["cache-control"] == "no-store"


def test_health_with_server_token_still_public(token_ctx):
    from fastapi.testclient import TestClient
    from aidoc.server.app import create_app
    with TestClient(create_app(token_ctx), client=("10.0.0.5", 5000), base_url="http://127.0.0.1:8765") as c:
        assert c.get("/health").status_code == 200
```

Append to `tests/api/test_system_settings.py`:

```python
def test_settings_compat_roundtrip_and_ranges(client):
    s = client.get("/api/settings").json()["settings"]
    assert s["compat"]["sync_timeout_s"] == 600
    r = client.put("/api/settings", json={"compat": {"sync_timeout_s": 900, "json_content": "doc4ai"}})
    assert r.status_code == 200 and r.json()["settings"]["compat"]["sync_timeout_s"] == 900
    for bad in [{"sync_timeout_s": 5}, {"sync_timeout_s": 4000}, {"max_file_mb": 0}, {"poll_max_wait_s": 301},
                {"json_content": "docling"}, {"enabled": 1}, {"task_retention_days": 0}]:
        r = client.put("/api/settings", json={"compat": bad})
        assert r.status_code == 422 and r.json()["error"] == "invalid_settings", (bad, r.text)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/compat/test_config.py tests/api/test_system_settings.py -k "compat or health" -q`
Expected: FAIL — `unknown config section compat` / 404 on `/health`.

- [ ] **Step 3: Implement**

`config.py`:

```python
@dataclass
class Compat:
    """[compat] docling-serve compatible API (spec 2026-10-05 compat §7)."""
    enabled: bool = True
    sync_timeout_s: int = 600
    max_file_mb: int = 200
    max_source_mb: int = 50
    embed_images_max_mb: int = 20
    max_concurrent_jobs_per_token: int = 8
    poll_max_wait_s: int = 60
    task_retention_days: int = 30
    json_content: str = "null"          # "null" | "doc4ai" (spike S1)
```

Register `"compat": Compat` in `_SECTIONS`, add `compat: Compat = field(default_factory=Compat)` to `AidocConfig` (before `root`). `settings.py`: extend `_MIN`/`_MAX` (`sync_timeout_s` 10–3600, `max_file_mb` 1–4096, `max_source_mb` 1–1024, `embed_images_max_mb` 1–1024, `max_concurrent_jobs_per_token` 1–100, `poll_max_wait_s` 1–300, `task_retention_days` 1–3650) and the enum table (where `lang`/`mineru_tier` are validated) gains `("compat", "json_content"): {"null", "doc4ai"}`.

`app.py` (pull/rebase first; three edits only): in `EnvelopeMiddleware.__call__` extend the passthrough tuple to `("/mcp/", "/.well-known/", "/v1/", "/v1alpha/")` and add `or path == "/health"`; in `create_app` before `_mount_web`:

```python
    @app.get("/health", include_in_schema=False)
    def health():
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/compat tests/api/test_system_settings.py tests/api/test_app_basics.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aidoc/config.py aidoc.toml src/aidoc/server/api/settings.py src/aidoc/server/app.py tests/compat/__init__.py tests/compat/test_config.py tests/api/test_system_settings.py
git commit -m "feat(compat): [compat] config section and public GET /health

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0175gLq9uam6M5Z9yNNxM4Ba"
```

---

### Task 3: Schema v5 — `compat_tasks` + Store methods + retention

**Files:**
- Modify: `src/aidoc/store.py` (`SCHEMA_VERSION = 5`, DDL, three methods)
- Modify: `src/aidoc/server/maintenance.py` (`run_once` prunes compat tasks)
- Test: `tests/compat/test_store_v5.py`; update the assertion `schema_version == "4"` in `tests/api/test_app_basics.py` to `"5"`

- [ ] **Step 1: Write the failing tests**

```python
import time

from aidoc.models import ConvertOptions


def _opts(tmp_root):
    return ConvertOptions(output_dir=tmp_root / "out")


def test_schema_v5_and_compat_task_roundtrip(tmp_root):
    from aidoc.store import Store
    s = Store(tmp_root / "data" / "aidoc.db")
    assert s._q1("SELECT value FROM meta WHERE key='schema_version'")["value"] == "5"
    job = s.create_job(_opts(tmp_root), "compat")
    s.create_compat_task(job, token_id="tok1", options={"to_formats": ["md"], "placeholder": "\f"}, protocol="docling-serve-v1")
    row = s.get_compat_task(job)
    assert row["token_id"] == "tok1" and row["options"] == {"to_formats": ["md"], "placeholder": "\f"}
    assert row["protocol"] == "docling-serve-v1" and s.get_compat_task("nope") is None
    assert s.get_job(job)["origin"] == "compat"


def test_prune_compat_tasks(tmp_root):
    from aidoc.store import Store
    s = Store(tmp_root / "data" / "aidoc.db")
    old = s.create_job(_opts(tmp_root), "compat")
    new = s.create_job(_opts(tmp_root), "compat")
    s.create_compat_task(old, token_id="t", options={}, protocol="docling-serve-v1")
    s.create_compat_task(new, token_id="t", options={}, protocol="docling-serve-v1")
    s._update("compat_tasks", "job_id", old, {"created_at": time.time() - 40 * 86400})
    assert s.prune_compat_tasks(older_than_ts=time.time() - 30 * 86400) == 1
    assert s.get_compat_task(old) is None and s.get_compat_task(new) is not None


def test_maintenance_prunes_compat_tasks(ctx):
    from aidoc.server.maintenance import Maintenance
    job = ctx.store.create_job(_opts(ctx.config.root), "compat")
    ctx.store.create_compat_task(job, token_id="t", options={}, protocol="docling-serve-v1")
    ctx.store._update("compat_tasks", "job_id", job, {"created_at": time.time() - 31 * 86400})
    res = Maintenance(ctx).run_once()
    assert res["compat_tasks_pruned"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/compat/test_store_v5.py -q` → FAIL (`AttributeError: create_compat_task`, version `"4"`).

- [ ] **Step 3: Implement**

`store.py`: `SCHEMA_VERSION = 5`; DDL `CREATE TABLE IF NOT EXISTS compat_tasks(job_id TEXT PRIMARY KEY, token_id TEXT NOT NULL, options_json TEXT NOT NULL, created_at REAL NOT NULL, protocol TEXT NOT NULL);`; `_JSON_COLS` gains `options_json`; methods:

```python
    def create_compat_task(self, job_id, *, token_id: str, options: dict, protocol: str) -> None:
        self._con.execute("INSERT OR REPLACE INTO compat_tasks(job_id, token_id, options_json, created_at, protocol) VALUES (?,?,?,?,?)",
                          (job_id, token_id, json.dumps(options, ensure_ascii=False), time.time(), protocol))
        self._con.commit()

    def get_compat_task(self, job_id) -> dict | None:
        return self._q1("SELECT * FROM compat_tasks WHERE job_id=?", (job_id,))

    def prune_compat_tasks(self, *, older_than_ts: float) -> int:
        cur = self._con.execute("DELETE FROM compat_tasks WHERE created_at < ?", (older_than_ts,))
        self._con.commit()
        return cur.rowcount
```

(Follow the file's existing lock/commit idiom — `_update`, `_q1` and the `threadsafe` RLock are already there.) `maintenance.run_once`: `res["compat_tasks_pruned"] = ctx.store.prune_compat_tasks(older_than_ts=now - ctx.config.compat.task_retention_days * 86400)`. Fix the `schema_version` assertion in `tests/api/test_app_basics.py`.

- [ ] **Step 4: Run** `uv run pytest tests/compat/test_store_v5.py tests/api/test_app_basics.py tests/mcp/test_store_v4.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aidoc/store.py src/aidoc/server/maintenance.py tests/compat/test_store_v5.py tests/api/test_app_basics.py
git commit -m "feat(store): schema v5 with compat_tasks for the docling-serve compatible API

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0175gLq9uam6M5Z9yNNxM4Ba"
```

---

### Task 4: Auth dependency, call logging helper, router skeleton, test fixtures

**Files:**
- Create: `src/aidoc/compat/__init__.py`, `src/aidoc/compat/auth.py`, `src/aidoc/compat/router.py` (skeleton: `/v1/capabilities` only)
- Modify: `src/aidoc/server/app.py` (`app.include_router(compat.router)` before `_mount_web`; install the logging wrapper)
- Create: `tests/compat/conftest.py`, `tests/compat/test_auth.py`

**Interfaces:**

```python
# aidoc/compat/auth.py
class CompatError(HTTPException): ...          # detail str, optional headers; rendered as {"detail": ...}
def compat_principal(request: Request) -> Principal   # FastAPI dependency (raises CompatError 400/401/403/429)
def compat_principal_read(request) -> Principal       # same, needs read OR convert (poll/result/assets)
@dataclass
class CompatCall:                               # request.state.compat
    principal: Principal | None; ts: float; started: float; protocol: str; ip: str | None; ua: str | None
    tool_name: str | None = None; job_id: str | None = None; args: dict | None = None
    status: str = "ok"; error_code: str | None = None; response_tokens_est: int | None = None; logged: bool = False
def finish_call(ctx, call: CompatCall, http_status: int, response_bytes: int) -> None   # one mcp_calls row
```

- [ ] **Step 1: Write the failing tests**

`tests/compat/conftest.py`:

```python
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from aidoc.mcp import tokens as T
from aidoc.mcp.tokens import load_or_create_secret
from tests.mcp.conftest import TINY_PNG, make_doc, page_md  # noqa: F401  re-exported fixtures/helpers


@pytest.fixture
def compat_env(ctx, monkeypatch):
    """ctx (fake engines, workers off) + PAT issuer + TestClient against the app with /v1 mounted."""
    from fastapi.testclient import TestClient
    from aidoc.server.app import create_app
    monkeypatch.setenv("AIDOC_TEST_ROUTES", "1")          # registers GET /v1/_whoami
    secret = load_or_create_secret(ctx.config.data_dir)

    def issue(scopes=("doc4ai:read", "doc4ai:convert"), name="compat token", expires_in=3600):
        raw = T.generate_token()
        tid = ctx.store.create_api_token(name=name, prefix=T.display_prefix(raw), token_hash=T.token_hash(secret, raw),
                                         scopes=list(scopes), expires_at=time.time() + expires_in)
        return raw, tid
    app = create_app(ctx)
    with TestClient(app, client=("10.1.2.3", 40000), base_url="http://127.0.0.1:8765") as c:
        yield SimpleNamespace(ctx=ctx, issue=issue, client=c, app=app)


def last_call(ctx):
    rows = ctx.store.list_mcp_calls(limit=1)
    return rows[0] if rows else None
```

`tests/compat/test_auth.py`:

```python
import pytest

from tests.compat.conftest import last_call


def test_capabilities_is_public(compat_env):
    r = compat_env.client.get("/v1/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["to_formats"] == ["md", "text"] and "workspace" not in body and "doc4ai" in body


@pytest.mark.parametrize("header", ["X-Api-Key", "Authorization"])
def test_token_accepted_in_either_header(compat_env, header):
    raw, tid = compat_env.issue()
    value = raw if header == "X-Api-Key" else f"Bearer {raw}"
    r = compat_env.client.get("/v1/_whoami", headers={header: value})
    assert r.status_code == 200 and r.json()["token_id"] == tid
    row = last_call(compat_env.ctx)
    assert row["method"] == "compat/docling" and row["status"] == "ok" and row["token_id"] == tid
    assert row["protocol_version"] == "docling-serve-v1"


def test_two_different_tokens_is_401(compat_env):
    a, _ = compat_env.issue()
    b, _ = compat_env.issue()
    r = compat_env.client.get("/v1/_whoami", headers={"X-Api-Key": a, "Authorization": f"Bearer {b}"})
    assert r.status_code == 401 and r.json() == {"detail": "X-Api-Key and Authorization disagree"}


def test_missing_bad_revoked_expired(compat_env):
    c = compat_env.client
    r = c.get("/v1/_whoami")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith('Bearer realm="doc4ai", error="invalid_token"')
    assert "resource_metadata" not in r.headers["www-authenticate"]
    assert last_call(compat_env.ctx)["error_code"] == "missing_token"
    raw, tid = compat_env.issue()
    compat_env.ctx.store.revoke_api_token(tid, "test", at=0)
    r = c.get("/v1/_whoami", headers={"X-Api-Key": raw})
    assert r.status_code == 401 and r.json()["detail"] == "This token was revoked"
    assert last_call(compat_env.ctx)["token_id"] == tid          # known token: attributed (MCP A phase item 14)
    raw2, _ = compat_env.issue(expires_in=-5)
    assert c.get("/v1/_whoami", headers={"X-Api-Key": raw2}).status_code == 401


def test_token_in_query_is_400(compat_env):
    raw, _ = compat_env.issue()
    r = compat_env.client.get("/v1/_whoami", params={"api_key": raw})
    assert r.status_code == 400 and "X-Api-Key" in r.json()["detail"]
    row = last_call(compat_env.ctx)
    assert row["error_code"] == "token_in_query" and raw not in (row["token_prefix_seen"] or "")


def test_scope_enforced(compat_env):
    raw, _ = compat_env.issue(scopes=("doc4ai:read",))
    r = compat_env.client.get("/v1/_whoami", headers={"X-Api-Key": raw})     # _whoami needs convert
    assert r.status_code == 403 and r.json() == {"detail": "token lacks doc4ai:convert"}
    assert last_call(compat_env.ctx)["status"] == "forbidden_scope"


def test_cross_origin_is_403_and_server_token_is_not_a_pat(compat_env):
    raw, _ = compat_env.issue()
    r = compat_env.client.get("/v1/_whoami", headers={"X-Api-Key": raw, "Origin": "http://evil.example"})
    assert r.status_code == 403
    r = compat_env.client.get("/v1/_whoami", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 401


def test_pat_never_authorizes_api(token_ctx):
    """A PAT on /api/* is still 401 even though /v1 now accepts it (MCP spec 2.1 isolation)."""
    from fastapi.testclient import TestClient
    from aidoc.server.app import create_app
    from aidoc.mcp import tokens as T
    from aidoc.mcp.tokens import load_or_create_secret
    secret = load_or_create_secret(token_ctx.config.data_dir)
    raw = T.generate_token()
    token_ctx.store.create_api_token(name="t", prefix=T.display_prefix(raw), token_hash=T.token_hash(secret, raw),
                                     scopes=["doc4ai:read", "doc4ai:convert", "doc4ai:manage"], expires_at=None)
    with TestClient(create_app(token_ctx), client=("10.0.0.9", 5000), base_url="http://127.0.0.1:8765") as c:
        assert c.get("/api/system", headers={"Authorization": f"Bearer {raw}"}).status_code == 401
        assert c.get("/api/system", headers={"X-Api-Key": raw}).status_code == 401


def test_auth_flood_429(compat_env):
    bad = "doc4ai_pat_" + "x" * 50
    for _ in range(10):
        compat_env.client.get("/v1/_whoami", headers={"X-Api-Key": bad})
    r = compat_env.client.get("/v1/_whoami", headers={"X-Api-Key": bad})
    assert r.status_code == 429 and r.headers["retry-after"]


def test_disabled_compat_is_404_but_health_ok(compat_env):
    compat_env.ctx.config.compat.enabled = False
    assert compat_env.client.get("/v1/capabilities").status_code == 404
    assert compat_env.client.get("/health").status_code == 200
```

- [ ] **Step 2: Run** `uv run pytest tests/compat/test_auth.py -q` → FAIL (404 everywhere).

- [ ] **Step 3: Implement**

`auth.py` (mirror `McpGate` step by step; reuse `ctx.extras["mcp"]` = `McpRuntime(verifier, limiter, recorder, guard)`):

```python
_FAILURE_TEXT = gate._FAILURE_TEXT                       # same wording as /mcp


def _www(text: str) -> dict:
    return {"WWW-Authenticate": www_authenticate("invalid_token", text)}   # resource_metadata=None until Spec 4 4b


def _principal(request: Request, *, scopes_any: tuple[str, ...]) -> Principal:
    ctx = request.app.state.ctx
    if not ctx.config.compat.enabled:
        raise CompatError(404, "compat API disabled")
    rt = ctx.extras["mcp"]
    call = CompatCall(principal=None, ts=time.time(), started=time.perf_counter(), protocol=_protocol(request.url.path),
                      ip=request.client.host if request.client else None, ua=request.headers.get("user-agent"))
    request.state.compat = call
    if {k.lower() for k in request.query_params} & {"api_key", "token", "access_token"}:
        _fail(ctx, rt, call, "token_in_query", 400, "send the token in X-Api-Key or Authorization", prefix=None)
    key = (request.headers.get("x-api-key") or "").strip() or None
    bearer = parse_bearer(request.headers.get("authorization"))
    if key and bearer and key != bearer:
        _fail(ctx, rt, call, "bad_format", 401, "X-Api-Key and Authorization disagree", prefix=T.prefix_seen(key))
    result = rt.verifier.verify(key or bearer)
    if isinstance(result, AuthFailure):
        _fail(ctx, rt, call, result.reason, 401, _FAILURE_TEXT[result.reason], prefix=result.prefix_seen, token_id=result.token_id)
    origin = request.headers.get("origin")
    if origin is not None and not same_origin(origin, request.headers.get("host")):
        call.status, call.error_code = "protocol_error", "http_403"
        finish_call(ctx, call, 403, 0)
        raise CompatError(403, "cross-origin request")
    if not any(result.has(s) for s in scopes_any):
        call.principal, call.status, call.error_code = result, "forbidden_scope", "forbidden_scope"
        finish_call(ctx, call, 403, 0)
        raise CompatError(403, f"token lacks {scopes_any[0]}")
    call.principal = result
    return result
```

`_fail()` runs `rt.guard.on_failure(ip, reason, prefix, call.protocol)` → `retry` not None → record the aggregate (the guard did) and raise `CompatError(429, f"{text}. Too many failed authentications; retry after {retry} s", headers={"Retry-After": str(retry), **_www(text)})`; else `rt.recorder.record(status="auth_error", error_code=reason, http_status=status, ip=…, token_id=…, token_prefix_seen=prefix, protocol_version=call.protocol, method="compat/docling", ts=call.ts)`, `call.logged = True`, raise `CompatError(status, text, headers=_www(text) if status == 401 else None)`.

`finish_call()` → `recorder.record(status=call.status, error_code=call.error_code, http_status=…, method="compat/docling", tool_name=call.tool_name, token_id=call.principal.token_id if call.principal else None, ip=call.ip, protocol_version=call.protocol, job_id=call.job_id, args=call.args, duration_ms=int((perf_counter() - call.started) * 1000), response_bytes=…, response_tokens_est=call.response_tokens_est, ts=call.ts)`; then `recorder.touch_token(token_id, ip, ua)` and `recorder.note_client(token_id=…, client_name, client_version, protocol_version=call.protocol, user_agent=ua, ip=ip)` with `(client_name, client_version) = calllog.ua_identity(ua)` (Task 10 adds `ua_identity`; until then a local `_ua()` returning the first product token). Sets `call.logged = True`.

`CompatLogMiddleware` (pure ASGI, in `auth.py`): for `scope["type"] == "http"` and path starting `/v1`: wrap `send` to capture status and body bytes; after `await app(...)`, `call = scope["state"].get("compat")` (Starlette's `request.state` is `scope["state"]`), and if `call and not call.logged` → `finish_call(ctx, call, status, bytes)`. `CompatError` → FastAPI's default `HTTPException` handler already renders `{"detail": …}` — but `app.py` has `@app.exception_handler(StarletteHTTPException)` that rewrites to `{"error": …}` + envelope. Add at the top of that handler: `if isinstance(exc, CompatError): return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)` (the Envelope passthrough already covers `/v1`).

`router.py`: `router = APIRouter()`; `GET /v1/capabilities` (spec §4.1 body; `engines` = `{n: e.available() for n, e in get_engines(cfg).items()}`); `GET /v1/_whoami` registered only when `os.environ.get("AIDOC_TEST_ROUTES") == "1"` (`Depends(compat_principal)` → `{"token_id": p.token_id}`). `app.py` (pull/rebase first): `from aidoc.compat.router import router as compat_router`; `app.include_router(compat_router)` right after the `/mcp` route; `app.add_middleware(CompatLogMiddleware)` **before** `app.add_middleware(EnvelopeMiddleware)` (Starlette: last added = outermost; Compat must be inner-most so it sees the real status).

- [ ] **Step 4: Run** `uv run pytest tests/compat -q && uv run pytest tests/mcp/test_security.py tests/api/test_security.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/aidoc/compat src/aidoc/server/app.py tests/compat/conftest.py tests/compat/test_auth.py
git commit -m "feat(compat): PAT auth dependency (X-Api-Key / Bearer), call logging and /v1/capabilities

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_0175gLq9uam6M5Z9yNNxM4Ba"
```

---

### Task 5: Options parsing (`compat/options.py`)

**Files:** Create `src/aidoc/compat/options.py`; test `tests/compat/test_options.py`.

**Interfaces:**

```python
@dataclass
class CompatOptions:
    to_formats: list[str]                 # subset of {"md", "text"} actually produced; "md" always present
    unsupported_formats: list[str]
    placeholder: str                      # md_page_break_placeholder ("" = none)
    keep_page_markers: bool
    image_mode: str                       # placeholder | referenced | embedded
    lang: str                             # cht | en
    engine: str | None
    force: bool
    timeout_s: int | None
    warnings: list[str]
    ignored_options: list[str]
    def to_json(self) -> dict: ...
    @classmethod
    def from_json(cls, d: dict) -> "CompatOptions": ...

def parse_options(raw: Mapping[str, Any], cfg, *, multipart: bool) -> CompatOptions   # raises CompatError(422)
LANG_MAP = {"zh-hant": "cht", "zh-tw": "cht", "zh": "cht", "cht": "cht", "zh-hk": "cht", "en": "en", "en-us": "en", "en-gb": "en"}
KNOWN_IGNORED = {"from_formats", "do_ocr", "ocr_engine", "ocr_preset", "pdf_backend", "table_mode", "abort_on_error",
                 "do_table_structure", "include_images", "include_page_images", "images_scale", "do_picture_description",
                 "picture_description_preset", "do_picture_classification", "do_code_enrichment", "do_formula_enrichment",
                 "do_chart_extraction", "pipeline", "do_pdf_heading_hierarchy", "md_compact_tables", "chunking_options",
                 "chunking_preset", "do_chunking", "page_range", "target_type", "target", "callbacks"}
```

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from aidoc.config import AidocConfig


@pytest.fixture
def cfg():
    return AidocConfig()


def test_defaults(cfg):
    from aidoc.compat.options import parse_options
    o = parse_options({}, cfg, multipart=False)
    assert o.to_formats == ["md"] and o.placeholder == "" and o.image_mode == "placeholder"
    assert o.lang == "cht" and o.engine is None and o.force is False and o.ignored_options == []


def test_openwebui_form_fields(cfg):
    from aidoc.compat.options import parse_options
    o = parse_options({"image_export_mode": "placeholder", "md_page_break_placeholder": "\f", "doc4ai_lang": "en",
                       "pdf_backend": "dlparse_v4", "table_mode": "accurate"}, cfg, multipart=True)
    assert o.placeholder == "\f" and o.lang == "en" and o.ignored_options == ["pdf_backend", "table_mode"]


def test_to_formats_list_forms_and_unsupported(cfg):
    from aidoc.compat.options import parse_options
    assert parse_options({"to_formats": ["json", "md", "text"]}, cfg, multipart=False).unsupported_formats == ["json"]
    assert parse_options({"to_formats": "md,text"}, cfg, multipart=True).to_formats == ["md", "text"]
    assert parse_options({"to_formats": ["text"]}, cfg, multipart=False).to_formats == ["md", "text"]
    with pytest.raises(Exception) as e:
        parse_options({"to_formats": 5}, cfg, multipart=False)
    assert getattr(e.value, "status_code", None) == 422


def test_ocr_lang_and_force_ocr(cfg, tmp_root):
    from aidoc.compat.options import parse_options
    assert parse_options({"ocr_lang": ["zh-Hant", "en"]}, cfg, multipart=False).lang == "cht"
    assert parse_options({"ocr_lang": ["en"]}, cfg, multipart=False).lang == "en"
    assert parse_options({"ocr_lang": ["fr"]}, cfg, multipart=False).lang == cfg.general.lang
    o = parse_options({"force_ocr": "true"}, cfg, multipart=True)          # no mineru .ready under tmp_root
    assert o.engine is None and any("force_ocr" in w for w in o.warnings)


def test_extensions_and_bools(cfg):
    from aidoc.compat.options import parse_options
    o = parse_options({"doc4ai_engine": "docling", "doc4ai_force": "1", "doc4ai_keep_page_markers": "yes",
                       "image_export_mode": "embedded", "document_timeout": "120"}, cfg, multipart=True)
    assert o.engine == "docling" and o.force and o.keep_page_markers and o.image_mode == "embedded" and o.timeout_s == 120
    with pytest.raises(Exception):
        parse_options({"doc4ai_engine": "tesseract"}, cfg, multipart=False)
    with pytest.raises(Exception):
        parse_options({"image_export_mode": "svg"}, cfg, multipart=False)


def test_roundtrip_json(cfg):
    from aidoc.compat.options import CompatOptions, parse_options
    o = parse_options({"md_page_break_placeholder": "\f", "to_formats": ["md", "text"], "page_range": [1, 3]}, cfg, multipart=False)
    assert "page_range" in o.ignored_options and any("page_range" in w for w in o.warnings)
    assert CompatOptions.from_json(o.to_json()) == o
```

- [ ] **Step 2: Run** `uv run pytest tests/compat/test_options.py -q` → FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** per spec §4.2: `_bool()` accepts bools and `true/false/1/0/yes/no` (else 422); `_list()` accepts list / comma string / repeated multipart values; `to_formats` → produced = `["md"] + (["text"] if "text" in req)`, unsupported = the rest in request order; `document_timeout` → `min(int, cfg.compat.sync_timeout_s)`; `force_ocr` → `engine="mineru"` only if `paths.ready_marker("mineru").exists()` else a warning; `page_range`, `do_ocr=false`, `do_chunking` → warning + `ignored_options`; `KNOWN_IGNORED` and unknown keys → `ignored_options` (sorted, deduped); `doc4ai_engine` must be one of `ENGINE_NAMES`, `doc4ai_lang` one of `cht|en`, `image_export_mode` one of the three → 422 `CompatError` otherwise. `to_json`/`from_json` via `dataclasses.asdict`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(compat): docling options → ConvertOptions translation` (trailer as in §0).

---

### Task 6: Markdown assembly and response models (`compat/render.py`)

**Files:** Create `src/aidoc/compat/render.py`; modify `src/aidoc/mcp/docs.py` (export `asset_refs(md) -> list[tuple[span, name]]` built on `_ASSET_MD` / `_ASSET_HTML`); test `tests/compat/test_render.py`.

**Interfaces:**

```python
class ErrorItem(BaseModel): component_type: str = "doc4ai"; module_name: str = "pipeline"; error_message: str; category: str = "UNKNOWN"; page_no: int | None = None
class ExportDocument(BaseModel): filename: str; md_content: str | None = None; json_content: dict | None = None; html_content: str | None = None; text_content: str | None = None; doctags_content: str | None = None; doclang_content: str | None = None; doc4ai: dict | None = None
STATUS = Literal["success", "partial_success", "failure", "pending", "started", "skipped"]
class ConvertDocumentResponse(BaseModel): document: ExportDocument; status: STATUS; errors: list[ErrorItem] = []; processing_time: float; timings: dict = {}; doc4ai: dict | None = None
class TaskStatusResponse(BaseModel): task_id: str; task_type: str = "convert"; task_status: STATUS; task_position: int | None = None; task_meta: dict | None = None; error_message: str | None = None
class TaskFailureResult(BaseModel): kind: Literal["TaskFailureResult"] = "TaskFailureResult"; failure: dict

def assemble_markdown(md: str, pages: int | None, opts: CompatOptions, *, doc_id: str, asset_base: str | None, assets_dir: Path | None, cfg) -> tuple[str, list[str]]
def text_from_markdown(md: str) -> str
def doc4ai_block(doc_row: dict, task_row: dict | None, opts: CompatOptions, *, cached: bool, attached: bool, elapsed_s: float, warnings: list[str]) -> dict
def build_response(ctx, request, doc_row: dict, task_row: dict | None, opts: CompatOptions, *, cached: bool, attached: bool, started: float) -> ConvertDocumentResponse
def failure_response(filename: str, task_row: dict | None, message: str, started: float) -> ConvertDocumentResponse
```

- [ ] **Step 1: Write the failing tests**

```python
from aidoc.compat.options import CompatOptions
from tests.mcp.conftest import TINY_PNG, page_md

BASE = dict(to_formats=["md"], unsupported_formats=[], placeholder="", keep_page_markers=False, image_mode="placeholder",
            lang="cht", engine=None, force=False, timeout_s=None, warnings=[], ignored_options=[])


def opts(**kw):
    return CompatOptions(**{**BASE, **kw})


def doc3():
    return "# Title\n\n" + "".join(page_md(n, heading=f"Chapter {n}", body=f"Body {n}. ![](assets/p{n}_1.png)\n") for n in (1, 2, 3))


def asm(md, pages, o, **kw):
    from aidoc.compat.render import assemble_markdown
    return assemble_markdown(md, pages, o, doc_id=kw.get("doc_id", "d"), asset_base=kw.get("asset_base"),
                             assets_dir=kw.get("assets_dir"), cfg=kw.get("cfg"))


def test_default_mode_keeps_markers_and_placeholders_images():
    md, warns = asm(doc3(), 3, opts())
    assert md.count("<!-- page: ") == 3 and "<!-- image -->" in md and "assets/" not in md and warns == []


def test_placeholder_between_pages_not_before_first():
    md, _ = asm(doc3(), 3, opts(placeholder="\f"))
    parts = md.split("\f")
    assert len(parts) == 3 and not md.startswith("\f") and "<!-- page:" not in md
    assert "Title" in parts[0] and "Body 1" in parts[0] and "Body 3" in parts[2]


def test_missing_pages_become_empty_segments():
    md = "# T\n" + page_md(1) + page_md(3)                      # page 2 has no marker
    out, _ = asm(md, 3, opts(placeholder="\f"))
    parts = out.split("\f")
    assert len(parts) == 3 and parts[1].strip() == "" and "Page 3" in parts[2]


def test_placeholder_count_is_pages_minus_one_even_if_text_contains_it():
    md = page_md(1, body="code with \f inside") + page_md(2)
    out, _ = asm(md, 2, opts(placeholder="\f"))
    assert out.count("\f") == 2          # one from the text, one inserted


def test_keep_markers_with_placeholder_and_pageless_doc():
    out, _ = asm(doc3(), 3, opts(placeholder="\f", keep_page_markers=True))
    assert out.count("\f") == 2 and out.count("<!-- page: ") == 3
    out, _ = asm("# docx\n\nno pages", None, opts(placeholder="\f"))
    assert "\f" not in out and "no pages" in out


def test_referenced_and_embedded_images(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    for n in (1, 2, 3):
        (assets / f"p{n}_1.png").write_bytes(TINY_PNG)
    out, _ = asm(doc3(), 3, opts(image_mode="referenced"), doc_id="d1", asset_base="http://h:8765", assets_dir=assets)
    assert "](http://h:8765/v1/doc4ai/documents/d1/assets/p1_1.png)" in out

    class C:
        pass
    cfg = C(); cfg.compat = C(); cfg.compat.embed_images_max_mb = 20
    out, warns = asm(doc3(), 3, opts(image_mode="embedded"), doc_id="d1", assets_dir=assets, cfg=cfg)
    assert out.count("](data:image/png;base64,") == 3 and warns == []
    cfg.compat.embed_images_max_mb = 0
    out, warns = asm(doc3(), 3, opts(image_mode="embedded"), doc_id="d1", assets_dir=assets, cfg=cfg)
    assert "<!-- image -->" in out and any("embed" in w for w in warns)


def test_text_content_and_models():
    from aidoc.compat.render import ConvertDocumentResponse, ExportDocument, text_from_markdown
    txt = text_from_markdown("# H\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n**bold** <!-- page: 1 -->")
    assert "#" not in txt and "|" not in txt and "bold" in txt and "page:" not in txt
    r = ConvertDocumentResponse(document=ExportDocument(filename="x.pdf", md_content="m"), status="success", processing_time=1.0)
    d = r.model_dump()
    assert d["document"]["json_content"] is None and d["errors"] == [] and d["timings"] == {}


def test_doc4ai_block_and_low_quality_status(make_doc, ctx):
    from aidoc.compat.render import build_response
    from starlette.requests import Request
    doc = make_doc("lowdoc", pages=2, status="low", quality={"score": 0.3, "level": "low", "reasons": ["chars_per_page"], "pages": []})
    req = Request({"type": "http", "scheme": "http", "headers": [(b"host", b"h:8765")], "path": "/v1/convert/file",
                   "query_string": b"", "server": ("h", 8765), "client": ("1.2.3.4", 1)})
    r = build_response(ctx, req, doc, None, opts(), cached=False, attached=False, started=0.0)
    assert r.status == "partial_success" and r.errors[0].category == "quality" and "chars_per_page" in r.errors[0].error_message
    assert r.document.doc4ai["doc_id"] == doc["id"] and r.document.doc4ai["quality"]["level"] == "low"
    assert r.doc4ai == {"doc_id": doc["id"], "quality_level": "low", "cached": False}
```

- [ ] **Step 2: Run** `uv run pytest tests/compat/test_render.py -q` → FAIL.
- [ ] **Step 3: Implement.** `assemble_markdown`: `pre, sections = pagemap.split_pages(md)`; page-less or `pages is None` → `units = None`; else `units = [sections.get(p, "") for p in range(1, pages + 1)]`, `units[0] = pre + units[0]`. Placeholder mode: strip `<!-- page: N -->` lines per unit (regex `^<!-- page: \d+ -->\n?` with MULTILINE) unless `keep_page_markers` (then re-emit `<!-- page: N -->\n` + unit), join with `opts.placeholder`. Default mode: original `md`. Images via `docs.asset_refs()`: `placeholder` → `<!-- image -->`; `referenced` → `f"{asset_base}/v1/doc4ai/documents/{doc_id}/assets/{name}"`; `embedded` → `data:<mime from suffix>;base64,…` reading `assets_dir / name`, stop embedding (placeholder for the rest + one warning `"embedded images exceed embed_images_max_mb; remaining images are placeholders"`) when the running byte total exceeds `cfg.compat.embed_images_max_mb * MIB`; a missing file → placeholder + warning. `text_from_markdown` = `quality.strip_markup()` then `re.sub(r"\n{3,}", "\n\n", s).strip()`. `doc4ai_block` fills spec §4.4 (`page_warnings` from `docs.page_warnings(doc_row)` as `{page, reason, repaired}`; `untrusted_pages` = `quality.get("untrusted", {}).get("pages", [])`; `tried` = `[a["engine"] for a in task_row["tried"]]` when a task row exists). `build_response`: markdown from `docs.load_doc(ctx, doc_id)` (cache + `output_missing` → `failure_response`), `asset_base = f"{request.url.scheme}://{request.headers['host']}"` (Spec 4 later swaps in `public_url`), status per D6 and A2, `processing_time = time.time() - started`, top-level `doc4ai = {"doc_id", "quality_level", "cached"}`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(compat): markdown assembly with docling page-break semantics, image modes and response models`.

---

### Task 7: Sync endpoints — `/v1/convert/file`, `/v1/convert/source`, `/v1alpha/convert/source`

**Files:**
- Create: `src/aidoc/compat/jobs.py` (`reserve_job_waiting`, `start_job_for_file`, `task_to_docling_status`, `wait_or_attach`)
- Modify: `src/aidoc/mcp/tools_convert.py` (extract `start_job_for_file(ctx, filename, part_path, sha, size, opts, token_id, origin="mcp")`; `start_job_for_bytes` writes the part then calls it; `reserve_job(ctx, opts, token_id, *, origin="mcp", limit=None)`)
- Modify: `src/aidoc/compat/router.py` (three POST routes)
- Test: `tests/compat/test_sync.py`

**Interfaces:**

```python
# compat/jobs.py
def reserve_job_waiting(ctx, opts, token_id, *, deadline: float, origin="compat") -> str | None   # None = timed out
def start_job_for_file(ctx, filename, part_path: Path, sha: str, size: int, opts, token_id, *, origin="compat") -> tuple[str, str]  # (job_id, task_id)
def task_to_docling_status(task: dict) -> str
async def stream_upload(request, field="files", *, max_bytes) -> tuple[str, Path, int, str, str]   # (filename, part_path, size, sha256, mime); raises CompatError(413/422)
async def run_sync(ctx, request, call, *, filename, part_path, size, sha, opts) -> ConvertDocumentResponse | JSONResponse(504)
```

- [ ] **Step 1: Write the failing tests**

```python
import io
import json
import threading
import time

import pytest

from tests.compat.conftest import last_call
from tests.mcp.conftest import TINY_PNG

PDF = (b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
       b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
       b"trailer<</Root 1 0 R>>\n%%EOF\n")


def _post_file(env, raw, name="a.pdf", data=PDF, **form):
    return env.client.post("/v1/convert/file", files={"files": (name, io.BytesIO(data), "application/pdf")},
                           data=form, headers={"X-Api-Key": raw})


@pytest.fixture
def workers(compat_env):
    """The ctx fixture starts no worker; sync conversion needs the queue running (fake engines: ~0.3 s per file)."""
    compat_env.ctx.queue.start()
    yield compat_env
    compat_env.ctx.queue.stop()


def test_openwebui_shape_sync_success(workers):
    env = workers
    raw, tid = env.issue()
    r = _post_file(env, raw, image_export_mode="placeholder", md_page_break_placeholder="\f")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success" and body["errors"] == [] and body["document"]["filename"] == "a.pdf"
    md = body["document"]["md_content"]
    pages = body["document"]["doc4ai"]["pages"]
    assert md.count("\f") == pages - 1 and "<!-- page:" not in md and "<!-- image -->" in md
    assert body["document"]["json_content"] is None and "workspace" not in body
    assert body["doc4ai"]["doc_id"] == body["document"]["doc4ai"]["doc_id"]
    job = env.ctx.store.get_job(body["document"]["doc4ai"]["job_id"])
    assert job["origin"] == "compat"
    row = last_call(env.ctx)
    assert row["tool_name"] == "convert_file" and row["status"] == "ok" and row["job_id"] == job["id"]
    args = json.loads(row["args_summary"])
    assert args["filename"] == "a.pdf" and args["placeholder"] is True and "content" not in args


def test_source_shape_and_v1alpha(workers):
    import base64
    env = workers
    raw, _ = env.issue()
    b64 = base64.b64encode(PDF).decode()
    r = env.client.post("/v1/convert/source", json={"options": {"to_formats": ["json", "md", "text"], "do_chunking": True},
                                                    "sources": [{"kind": "file", "filename": "s.pdf", "base64_string": b64}]},
                        headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200, r.text
    d = r.json()["document"]
    assert d["md_content"] and d["text_content"] and d["json_content"] is None
    assert d["doc4ai"]["unsupported_formats"] == ["json"] and "do_chunking" in d["doc4ai"]["ignored_options"]
    assert d["doc4ai"]["cached"] is True                      # same sha as nothing? no: new file -> False
    r2 = env.client.post("/v1alpha/convert/source", json={"file_sources": [{"filename": "s.pdf", "base64_string": b64}]},
                         headers={"Authorization": f"Bearer {raw}"})
    assert r2.status_code == 200 and r2.json()["document"]["doc4ai"]["cached"] is True
    assert last_call(env.ctx)["protocol_version"] == "docling-serve-v1alpha"


def test_cache_hit_makes_no_job(workers):
    env = workers
    raw, _ = env.issue()
    first = _post_file(env, raw).json()
    n_jobs = len(env.ctx.store.list_jobs())
    second = _post_file(env, raw).json()
    assert second["document"]["doc4ai"]["cached"] is True and len(env.ctx.store.list_jobs()) == n_jobs
    assert second["document"]["doc4ai"]["doc_id"] == first["document"]["doc4ai"]["doc_id"]
    assert _post_file(env, raw, doc4ai_force="true").json()["document"]["doc4ai"]["cached"] is False


def test_unsupported_file_and_engine_failure_are_status_failure(workers, monkeypatch):
    env = workers
    raw, _ = env.issue()
    r = _post_file(env, raw, name="x.exe", data=b"MZ" + b"\0" * 64)
    assert r.status_code == 200 and r.json()["status"] == "failure"
    assert r.json()["errors"][0]["error_message"].startswith("unsupported_file")
    assert last_call(env.ctx)["status"] == "tool_error" and last_call(env.ctx)["error_code"] == "unsupported_file"
    from tests.fakes.scenario import write_scenario
    write_scenario(env.ctx.config.root / "sc.json", default="error")     # every engine fails
    r = _post_file(env, raw, name="fail.pdf", data=PDF + b"\n% fail\n")
    assert r.json()["status"] == "failure" and "engine" in r.json()["errors"][0]["error_message"]


def test_low_quality_is_partial_success(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="low")
    raw, _ = env.issue()
    body = _post_file(env, raw, name="low.pdf", data=PDF + b"\n% low\n").json()
    assert body["status"] == "partial_success" and body["errors"][0]["category"] == "quality"


def test_multiple_files_422_and_wrong_source_kind_422(compat_env):
    raw, _ = compat_env.issue()
    r = compat_env.client.post("/v1/convert/file", files=[("files", ("a.pdf", PDF)), ("files", ("b.pdf", PDF))], headers={"X-Api-Key": raw})
    assert r.status_code == 422 and r.json()["detail"] == "one file per request"
    r = compat_env.client.post("/v1/convert/source", json={"sources": [{"kind": "http", "url": "http://x"}]}, headers={"X-Api-Key": raw})
    assert r.status_code == 422 and r.json()["detail"].startswith("unsupported_source")


def test_oversized_stream_is_cut_and_part_deleted(compat_env):
    env = compat_env
    env.ctx.config.compat.max_file_mb = 1
    raw, _ = env.issue()
    big = PDF + b"\0" * (2 * 1024 * 1024)
    r = _post_file(env, raw, data=big)
    assert r.status_code == 413 and "1 MB" in r.json()["detail"]
    assert not list((env.ctx.uploads.dir).glob("*.part"))
    assert last_call(env.ctx)["error_code"] == "file_too_large"


def test_sync_timeout_504_then_async_result(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=3.0)
    env.ctx.config.compat.sync_timeout_s = 10          # validator min is 10; the test shortens the wait below
    env.ctx.config.compat.sync_timeout_s = 1
    raw, _ = env.issue()
    r = _post_file(env, raw, name="slow.pdf", data=PDF + b"\n% slow\n")
    assert r.status_code == 504
    body = r.json()
    assert body["task_id"] and body["status_url"] == f"/v1/status/poll/{body['task_id']}"
    assert last_call(env.ctx)["error_code"] == "sync_timeout"
    deadline = time.time() + 15
    while time.time() < deadline:
        st = env.client.get(f"/v1/status/poll/{body['task_id']}", headers={"X-Api-Key": raw}).json()
        if st["task_status"] == "success":
            break
        time.sleep(0.3)
    assert st["task_status"] == "success"
    assert env.client.get(f"/v1/result/{body['task_id']}", headers={"X-Api-Key": raw}).json()["status"] == "success"


def test_concurrent_same_file_waits_on_the_running_task(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=1.5)
    raw, _ = env.issue()
    results = {}

    def go(k):
        results[k] = _post_file(env, raw, name="same.pdf", data=PDF + b"\n% same\n").json()
    ts = [threading.Thread(target=go, args=(k,)) for k in range(2)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert {r["status"] for r in results.values()} == {"success"}
    assert len([j for j in env.ctx.store.list_jobs() if j["origin"] == "compat"]) == 1
    assert sorted(r["document"]["doc4ai"].get("attached", False) for r in results.values()) == [False, True]


def test_sync_waits_for_a_job_slot_instead_of_failing(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    env.ctx.config.compat.max_concurrent_jobs_per_token = 1
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=1.0)
    raw, _ = env.issue()
    results = []

    def go(n):
        results.append(_post_file(env, raw, name=f"f{n}.pdf", data=PDF + b"\n% " + str(n).encode()).status_code)
    ts = [threading.Thread(target=go, args=(n,)) for n in range(2)]
    [t.start() for t in ts]
    [t.join(40) for t in ts]
    assert results == [200, 200]


def test_revocation_mid_wait_does_not_abort_the_running_job(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=1.5)
    raw, tid = env.issue()
    out = {}
    t = threading.Thread(target=lambda: out.update(r=_post_file(env, raw, name="rev.pdf", data=PDF + b"\n% rev\n")))
    t.start()
    time.sleep(0.4)
    env.ctx.store.revoke_api_token(tid, "mid", at=time.time())
    t.join(30)
    assert out["r"].status_code == 200 and out["r"].json()["status"] == "success"
    assert _post_file(env, raw).status_code == 401
```

- [ ] **Step 2: Run** `uv run pytest tests/compat/test_sync.py -q` → FAIL (404).

- [ ] **Step 3: Implement**

`tools_convert.py` refactor (behaviour-preserving; `tests/mcp/test_tool_convert.py` must stay green): `reserve_job(ctx, opts, token_id, *, origin="mcp", limit=None)` passes `origin` to `Store.create_mcp_job` (add the `origin` parameter there; default `"mcp"`) and `limit or cfg.mcp.max_concurrent_jobs_per_token`; new `start_job_for_file(ctx, filename, part_path, sha, size, opts, token_id, *, origin="mcp", job_id=None)` does the `create_upload` → `update_upload(complete)` → `create_task_from_upload` → `_publish` dance on an already-written part file (reusing the reserved `job_id` when given); `start_job_for_bytes` = hash + write part + `start_job_for_file`.

`compat/jobs.py`:

```python
async def stream_upload(request, *, max_bytes: int):
    """Streams the single `files` part to UploadManager.part_path(<uuid>) with a byte cap (spike S4 approach).
    Any second file part → 422 one file per request; over cap → part deleted, 413."""
    ...

def reserve_job_waiting(ctx, opts, token_id, *, deadline, origin="compat"):
    limit = ctx.config.compat.max_concurrent_jobs_per_token
    while True:
        job_id = reserve_job_once(ctx, opts, token_id, origin=origin, limit=limit)     # Store.create_mcp_job → None when full
        if job_id or time.time() >= deadline:
            return job_id
        time.sleep(0.5)

async def run_sync(ctx, request, call, *, filename, part_path, size, sha, opts: CompatOptions):
    started = call.ts
    deadline = started + min(opts.timeout_s or 10**9, ctx.config.compat.sync_timeout_s)
    convert = build_convert_options(ctx.config, engine=opts.engine, lang=opts.lang, force=opts.force)
    out_dir = planned_output_dir(ctx.store, convert.output_dir, Path(filename), sha)
    if not opts.force and (doc := ctx.store.find_document(sha, str(out_dir))) and doc["status"] in VISIBLE:
        part_path.unlink(missing_ok=True); call.tool_name_done(...)
        return build_response(ctx, request, doc, None, opts, cached=True, attached=False, started=started)
    busy = ctx.store.busy_task_id(sha, str(out_dir))
    if busy:                                   # D4: attach to the running task
        part_path.unlink(missing_ok=True); job_id, task_id, attached = ctx.store.get_task(busy)["job_id"], busy, True
    else:
        try: check_magic(filename, part_path.read_bytes()[:16] ...)   # use read_head(part_path)
        except ToolFailure as f: part_path.unlink(); call.status, call.error_code = "tool_error", f.code; return failure_response(filename, None, f"{f.code}: {f.message}", started)
        job_id = await anyio.to_thread.run_sync(reserve_job_waiting, ctx, convert, call.principal.token_id, deadline=deadline)
        if job_id is None: part_path.unlink(); call.status, call.error_code = "tool_error", "sync_timeout"; return JSONResponse(504, {"detail": "no job slot became free", "task_id": None, ...})
        job_id, task_id = start_job_for_file(ctx, filename, part_path, sha, size, convert, token_id, origin="compat", job_id=job_id)
        ctx.store.create_compat_task(job_id, token_id=..., options=opts.to_json(), protocol=call.protocol)
        attached = False
    call.job_id = job_id
    task = await wait_for_task(ctx, task_id, int(deadline - time.time()), report=None)
    if task["status"] not in _TERMINAL:
        call.status, call.error_code = "tool_error", "sync_timeout"
        return JSONResponse({"detail": "conversion still running", "task_id": job_id, "status_url": f"/v1/status/poll/{job_id}", "result_url": f"/v1/result/{job_id}"}, status_code=504)
    return response_for_task(ctx, request, task, opts, started=started, attached=attached, call=call)
```

`response_for_task`: `done|low|skipped` → `build_response(... doc_row=find_document(sha, output_dir) ...)` (`skipped` with a `documents` row = pipeline-level cache hit); `failed|cancelled` → `failure_response(filename, task, f"{task['error_kind']}: {task['error_msg']}" or "cancelled", started)` and `call.status, call.error_code = "tool_error", "conversion_failed"`. `call.response_tokens_est = estimate_tokens(md)[0]`; `call.args = {"filename", "size", "sha256": sha[:8], "to_formats", "placeholder": bool(opts.placeholder), "lang", "engine"}`.

`router.py` routes: `POST /v1/convert/file` (`principal = Depends(compat_principal)`; `stream_upload` → `parse_options(form_fields, multipart=True)` — form fields are collected by the same streaming parser; `call.tool_name = "convert_file"`); `POST /v1/convert/source` (JSON body ≤ `max_source_mb` — check `Content-Length` first then `await request.body()` with a cap; exactly one `sources[]`, `kind == "file"` else 422 `unsupported_source: <kind>`; `target.kind` present and ≠ `inbody` → 422 `unsupported_target`; `decode_content(base64_string, limit)`; write the part; `call.tool_name = "convert_source"`); `POST /v1alpha/convert/source` (accepts `file_sources[]` or `sources[]`; `call.protocol = "docling-serve-v1alpha"`). All three end in `await run_sync(...)`. The `_protocol()` helper in `auth.py` maps `/v1alpha/` → `docling-serve-v1alpha`.

- [ ] **Step 4: Run** `uv run pytest tests/compat/test_sync.py tests/mcp/test_tool_convert.py tests/mcp/test_job_cap.py -q` → PASS.

- [ ] **Step 5: Commit** `feat(compat): synchronous /v1/convert/file and /v1/convert/source (+ v1alpha) on the shared pipeline`.

---

### Task 8: Async endpoints — `…/async`, `/v1/status/poll/{task_id}`, `/v1/result/{task_id}`

**Files:** Modify `src/aidoc/compat/router.py`, `src/aidoc/compat/jobs.py` (`submit_async`, `status_response`); test `tests/compat/test_async.py`.

- [ ] **Step 1: Write the failing tests**

```python
import io
import time

from tests.compat.conftest import last_call
from tests.compat.test_sync import PDF, workers  # noqa: F401


def _submit(env, raw, name="a.pdf", data=PDF, **form):
    return env.client.post("/v1/convert/file/async", files={"files": (name, io.BytesIO(data), "application/pdf")},
                           data=form, headers={"X-Api-Key": raw})


def test_async_lifecycle(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=1.0)
    raw, _ = env.issue()
    r = _submit(env, raw, md_page_break_placeholder="\f")
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["task_type"] == "convert" and st["task_status"] in ("pending", "started") and st["task_position"] == 0
    tid = st["task_id"]
    assert last_call(env.ctx)["tool_name"] == "convert_file_async" and last_call(env.ctx)["job_id"] == tid
    r = env.client.get(f"/v1/result/{tid}", headers={"X-Api-Key": raw})
    assert r.status_code == 404 and r.json()["detail"] == "task not finished"
    r = env.client.get(f"/v1/status/poll/{tid}", params={"wait": 5}, headers={"X-Api-Key": raw})
    assert r.json()["task_status"] == "success" and r.json()["task_meta"]["pages_total"] >= 1
    res = env.client.get(f"/v1/result/{tid}", headers={"X-Api-Key": raw}).json()
    assert res["status"] == "success" and res["document"]["md_content"].count("\f") == res["document"]["doc4ai"]["pages"] - 1
    assert last_call(env.ctx)["tool_name"] == "result"


def test_poll_wait_is_capped_and_other_token_sees_404(workers):
    env = workers
    env.ctx.config.compat.poll_max_wait_s = 1
    raw, _ = env.issue()
    other, _ = env.issue()
    tid = _submit(env, raw).json()["task_id"]
    t0 = time.time()
    env.client.get(f"/v1/status/poll/{tid}", params={"wait": 60}, headers={"X-Api-Key": raw})
    assert time.time() - t0 < 5
    assert env.client.get(f"/v1/status/poll/{tid}", headers={"X-Api-Key": other}).status_code == 404
    assert env.client.get(f"/v1/result/{tid}", headers={"X-Api-Key": other}).status_code == 404
    assert env.client.get("/v1/status/poll/nope", headers={"X-Api-Key": raw}).json() == {"detail": "task not found"}


def test_async_failure_result(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    write_scenario(env.ctx.config.root / "sc.json", default="error")
    raw, _ = env.issue()
    tid = _submit(env, raw, name="bad.pdf", data=PDF + b"\n% bad\n").json()["task_id"]
    st = env.client.get(f"/v1/status/poll/{tid}", params={"wait": 10}, headers={"X-Api-Key": raw}).json()
    assert st["task_status"] == "failure" and st["error_message"].startswith("engine")
    res = env.client.get(f"/v1/result/{tid}", headers={"X-Api-Key": raw}).json()
    assert res["kind"] == "TaskFailureResult" and "engine" in res["failure"]["error_message"]


def test_async_cache_hit_and_cap_429(workers):
    from tests.fakes.scenario import write_scenario
    env = workers
    raw, _ = env.issue()
    first = _submit(env, raw).json()["task_id"]
    env.client.get(f"/v1/status/poll/{first}", params={"wait": 10}, headers={"X-Api-Key": raw})
    again = _submit(env, raw).json()
    assert again["task_status"] == "success"                       # cache hit: immediately resolvable
    assert env.client.get(f"/v1/result/{again['task_id']}", headers={"X-Api-Key": raw}).json()["document"]["doc4ai"]["cached"] is True
    env.ctx.config.compat.max_concurrent_jobs_per_token = 1
    write_scenario(env.ctx.config.root / "sc.json", default="slow_ok", delay_s=2.0)
    assert _submit(env, raw, name="s1.pdf", data=PDF + b"\n%1\n").status_code == 200
    r = _submit(env, raw, name="s2.pdf", data=PDF + b"\n%2\n")
    assert r.status_code == 429 and r.headers["retry-after"] == "5"


def test_read_scope_is_enough_for_poll_and_result(workers):
    env = workers
    conv, _ = env.issue()
    reader, _ = env.issue(scopes=("doc4ai:read",))
    tid = _submit(env, conv).json()["task_id"]
    assert env.client.get(f"/v1/status/poll/{tid}", headers={"X-Api-Key": reader}).status_code == 404   # not its task
    assert env.client.post("/v1/convert/file/async", files={"files": ("a.pdf", io.BytesIO(PDF))}, headers={"X-Api-Key": reader}).status_code == 403
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `submit_async` = the sync prelude (stream/decode, cache check, busy check, magic, single `reserve_job_once` → `None` → `CompatError(429, "too many jobs in progress", headers={"Retry-After": "5"})`, start job, `create_compat_task`) returning `TaskStatusResponse(task_id=job_id, task_status=task_to_docling_status(task), task_position=position)`; cache hit creates **no job** but must still be resolvable: insert a `compat_tasks` row with `job_id = "cached:" + doc_id`? — no: keep `task_id` opaque by creating a `compat_tasks` row keyed `f"c{sha[:31]}"` with `options_json` + `doc_id` (`options["_cached_doc_id"]`); `status/poll` and `result` treat `_cached_doc_id` as success. `position` = number of `queued` tasks created before this one (`Store.count_queued_tasks()` minus later ones; simplest: `SELECT COUNT(*) FROM tasks WHERE status='queued' AND created_at < ?`). `GET /v1/status/poll/{task_id}?wait=W`: `Depends(compat_principal_read)`; row = `get_compat_task` → `row["token_id"] != principal.token_id` → 404 `task not found`; loop until terminal or `min(W, cfg.compat.poll_max_wait_s)`; `task_meta = serialize_task(...)["progress"]`; `error_message` for failures. `GET /v1/result/{task_id}`: not terminal → 404 `task not finished`; `failed|cancelled` → `TaskFailureResult(failure={"error_kind", "error_message", "tried"})`; else `build_response` with `CompatOptions.from_json(row["options"])`. Tool names: `convert_file_async`, `convert_source_async`, `status_poll`, `result`.
- [ ] **Step 4: Run** `uv run pytest tests/compat -q` → PASS.
- [ ] **Step 5: Commit** `feat(compat): async convert, status poll and result endpoints`.

---

### Task 9: Referenced assets endpoint

**Files:** Modify `src/aidoc/compat/router.py`; test `tests/compat/test_assets.py`.

- [ ] **Step 1: Write the failing tests**

```python
from tests.mcp.conftest import TINY_PNG


def test_asset_download_needs_read_scope_and_is_safe(compat_env, make_doc):
    doc = make_doc("imgdoc", pages=1)
    raw, _ = compat_env.issue(scopes=("doc4ai:read",))
    r = compat_env.client.get(f"/v1/doc4ai/documents/{doc['id']}/assets/p1_1.png", headers={"X-Api-Key": raw})
    assert r.status_code == 200 and r.content == TINY_PNG and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
    assert compat_env.client.get(f"/v1/doc4ai/documents/{doc['id']}/assets/..%2F{doc['id']}.md", headers={"X-Api-Key": raw}).status_code == 400
    assert compat_env.client.get(f"/v1/doc4ai/documents/{doc['id']}/assets/nope.png", headers={"X-Api-Key": raw}).status_code == 404
    assert compat_env.client.get("/v1/doc4ai/documents/zzz/assets/p1_1.png", headers={"X-Api-Key": raw}).status_code == 404
    assert compat_env.client.get(f"/v1/doc4ai/documents/{doc['id']}/assets/p1_1.png").status_code == 401


def test_referenced_mode_links_resolve(compat_env, make_doc):
    """A referenced-mode response links to this endpoint with the request's scheme/host."""
    import io
    from tests.compat.test_sync import PDF
    compat_env.ctx.queue.start()
    try:
        raw, _ = compat_env.issue()
        r = compat_env.client.post("/v1/convert/file", files={"files": ("r.pdf", io.BytesIO(PDF), "application/pdf")},
                                   data={"image_export_mode": "referenced"}, headers={"X-Api-Key": raw, "Host": "doc4ai.lan:8765"})
        md = r.json()["document"]["md_content"]
        assert "](http://doc4ai.lan:8765/v1/doc4ai/documents/" in md
        url = md.split("](")[1].split(")")[0]
        assert compat_env.client.get(url.replace("http://doc4ai.lan:8765", ""), headers={"X-Api-Key": raw}).status_code == 200
    finally:
        compat_env.ctx.queue.stop()
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Route `GET /v1/doc4ai/documents/{doc_id}/assets/{path:path}` with `Depends(compat_principal_assets)` (`("doc4ai:read",)`): reuse `aidoc.server.api.documents.get_asset`'s path rules (`safe_relative` from `app.py`; 400 `bad path`) and `safe_file_response` (nosniff + CSP sandbox, inline only for images); unknown doc or hidden status → 404; `call.tool_name = "asset"`, `call.args = {"doc_id", "name"}`. Note the `Host` header in the test: `TestClient` passes it through, and `auth.host_ok` only applies when no server token is configured — the asset route does not call `host_ok` (the PAT is the credential).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(compat): referenced-image asset endpoint under PAT auth`.

---

### Task 10: Admin visibility — User-Agent identity, status/snippets, Jobs origin badge, token dialog text

**Files:**
- Modify: `src/aidoc/mcp/calllog.py` (`ua_identity(ua) -> tuple[str, str | None]`; `compat/auth.finish_call` uses it)
- Modify: `src/aidoc/server/api/mcp.py` (`status()` adds `compat: {enabled, base_url, calls_24h}`)
- Modify: `src/aidoc/mcp/snippets.py` (`render_snippets` adds `{"client": "openwebui", "title": "Open WebUI（環境變數）", "language": "bash", "text": ...}` with `DOCLING_SERVER_URL=<origin of endpoint_url>`, `DOCLING_API_KEY=<token>`, `CONTENT_EXTRACTION_ENGINE=docling`; `readme_section` unchanged — README gets its own section in Task 12)
- Modify: `web/src/api/types.ts` (`Job.origin: "web" | "cli" | "mcp" | "compat"`; `McpStatus.compat`), `web/src/features/jobs/*` origin label map (`compat` → 「相容 API」), `web/src/features/mcp/OverviewTab.tsx` (one line under the endpoint card: 「docling-serve 相容 API：`<base_url>`」 + 24h count; hidden when `compat.enabled` is false), `web/src/features/mcp/CreateTokenDialog.tsx` (scope hint for `doc4ai:convert`: 「也允許 docling-serve 相容 API（Open WebUI 等）」), `web/src/features/mcp/ConfigSnippets.tsx` (renders whatever snippets the API returns — verify the new tab appears without code changes; else add the tab)
- Test: `tests/compat/test_logging_admin.py`, `tests/mcp/test_snippets.py` (append), Vitest `web/src/features/mcp/__tests__/compat.test.tsx`

- [ ] **Step 1: Write the failing tests**

```python
# tests/compat/test_logging_admin.py
import io

from tests.compat.test_sync import PDF, workers  # noqa: F401


def test_ua_identity():
    from aidoc.mcp.calllog import ua_identity
    assert ua_identity("python-requests/2.32.3") == ("python-requests", "2.32.3")
    assert ua_identity("Go-http-client/1.1") == ("Go-http-client", "1.1")
    assert ua_identity("curl/8.9.1 (x86_64)") == ("curl", "8.9.1")
    assert ua_identity("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36") == ("Mozilla", "5.0")
    assert ua_identity(None) == ("unknown", None) and ua_identity("") == ("unknown", None)
    assert len(ua_identity("x" * 500)[0]) <= 100


def test_compat_calls_show_up_in_clients_tokens_and_status(workers):
    env = workers
    raw, tid = env.issue(name="openwebui")
    env.client.post("/v1/convert/file", files={"files": ("a.pdf", io.BytesIO(PDF), "application/pdf")},
                    headers={"X-Api-Key": raw, "User-Agent": "python-requests/2.32.3"})
    clients = env.client.get("/api/mcp/clients").json()["clients"]
    me = [c for c in clients if c["token_id"] == tid]
    assert me and me[0]["client_name"] == "python-requests" and me[0]["protocol_version"] == "docling-serve-v1"
    tok = [t for t in env.client.get("/api/mcp/tokens").json()["tokens"] if t["id"] == tid][0]
    assert tok["calls_24h"] >= 1 and tok["last_client"] == "python-requests/2.32.3"
    status = env.client.get("/api/mcp/status").json()
    assert status["compat"]["enabled"] is True and status["compat"]["base_url"].endswith(":8765") and status["compat"]["calls_24h"] >= 1
    calls = env.client.get("/api/mcp/calls", params={"tool": "convert_file"}).json()["calls"]
    assert calls and calls[0]["method"] == "compat/docling" and calls[0]["token_name"] == "openwebui"
    jobs = env.client.get("/api/jobs").json()["jobs"]
    assert any(j["origin"] == "compat" for j in jobs)
```

Append to `tests/mcp/test_snippets.py`:

```python
def test_openwebui_snippet():
    from aidoc.mcp.snippets import render_snippets
    s = [x for x in render_snippets("http://box:8765/mcp", "doc4ai_pat_X") if x["client"] == "openwebui"][0]
    assert "DOCLING_SERVER_URL=http://box:8765" in s["text"] and "/v1" not in s["text"]
    assert "DOCLING_API_KEY=doc4ai_pat_X" in s["text"] and "CONTENT_EXTRACTION_ENGINE=docling" in s["text"]
```

Vitest (`compat.test.tsx`): renders `OverviewTab` with a status fixture containing `compat: {enabled: true, base_url: "http://h:8765", calls_24h: 3}` → text 「docling-serve 相容 API」 and `http://h:8765` present; with `enabled: false` → absent. Jobs list fixture with `origin: "compat"` → badge 「相容 API」.

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `ua_identity`: first whitespace-separated token, split on the first `/`, clamp name ≤ 100 / version ≤ 50 (the same caps `CallRecorder.note_client` enforces). `status()`: `compat = {"enabled": cfg.compat.enabled, "base_url": endpoint_urls(...)[0].removesuffix("/mcp"), "calls_24h": store.count_mcp_calls(since=now-86400, method="compat/docling")}` — add an optional `method=` filter to `Store.count_mcp_calls`. Web: type unions, label map, one Overview line, dialog hint.
- [ ] **Step 4: Run** `uv run pytest tests/compat tests/mcp/test_snippets.py tests/mcp/test_admin_tokens.py -q && pnpm --dir web test` → PASS.
- [ ] **Step 5: Commit** `feat(compat): compat calls in the MCP admin (clients, status, snippets), Jobs origin badge`.

---

### Task 11: Shape replay suite and OpenAPI snapshot validation

**Files:**
- Create: `tests/compat/shapes/openwebui_0_11_4.json`, `openwebui_old_single_doc.json`, `ragflow_1_0_v1_chunked.json`, `ragflow_1_0_v1alpha.json`, `haystack_docling_serve.json`, `kotaemon_doc4ai_loader.json`, `curl_async.json` — each `{"source": "<URL + version>", "method", "path", "headers": {...}, "form"?: {...}, "json"?: {...}, "file_field"?: "files", "expect": {...}}`
- Create: `tests/compat/test_shapes.py`
- Uses: `tests/compat/fixtures/docling_serve_openapi_1_36_0.json` (Task 1)

- [ ] **Step 1: Write the failing tests**

```python
import io
import json
from pathlib import Path

import jsonschema
import pytest

from tests.compat.test_sync import PDF, workers  # noqa: F401

SHAPES = sorted((Path(__file__).parent / "shapes").glob("*.json"))
OPENAPI = json.loads((Path(__file__).parent / "fixtures" / "docling_serve_openapi_1_36_0.json").read_text(encoding="utf-8"))


def _schema(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}", "components": OPENAPI["components"]}


def _replay(env, raw, shape: dict):
    headers = {k: v.replace("<TOKEN>", raw) for k, v in shape["headers"].items()}
    if shape.get("file_field"):
        return env.client.request(shape["method"], shape["path"], files={shape["file_field"]: ("a.pdf", io.BytesIO(PDF), "application/pdf")},
                                  data=shape.get("form", {}), headers=headers)
    body = json.loads(json.dumps(shape["json"]).replace("<B64>", __import__("base64").b64encode(PDF).decode()))
    return env.client.request(shape["method"], shape["path"], json=body, headers=headers)


@pytest.mark.parametrize("shape_path", SHAPES, ids=[p.stem for p in SHAPES])
def test_shape_replays_and_matches_docling_schema(workers, shape_path):
    env = workers
    raw, _ = env.issue()
    shape = json.loads(shape_path.read_text(encoding="utf-8"))
    r = _replay(env, raw, shape)
    assert r.status_code == shape["expect"]["status_code"], (shape_path.name, r.text)
    body = r.json()
    schema_name = shape["expect"]["schema"]              # ConvertDocumentResponse | TaskStatusResponse
    jsonschema.validate(body, _schema(schema_name))      # extra keys allowed (spike S2)
    for check in shape["expect"].get("checks", []):      # tiny expression language: {"path": "document.md_content", "op": "count", "needle": "\f", "equals": "pages-1"}
        _check(body, check)
    if schema_name == "ConvertDocumentResponse":
        assert body["status"] in OPENAPI["components"]["schemas"]["ConversionStatus"]["enum"]


def test_curl_async_shape(workers):
    """The async shape needs three requests; replayed by hand."""
    env = workers
    raw, _ = env.issue()
    shape = json.loads((Path(__file__).parent / "shapes" / "curl_async.json").read_text(encoding="utf-8"))
    r = _replay(env, raw, shape)
    jsonschema.validate(r.json(), _schema("TaskStatusResponse"))
    tid = r.json()["task_id"]
    st = env.client.get(f"/v1/status/poll/{tid}", params={"wait": 10}, headers={"X-Api-Key": raw}).json()
    jsonschema.validate(st, _schema("TaskStatusResponse"))
    res = env.client.get(f"/v1/result/{tid}", headers={"X-Api-Key": raw}).json()
    jsonschema.validate(res, _schema("ConvertDocumentResponse"))
```

`_check` supports `op ∈ {count, contains, not_contains, equals, is_null, list_contains}` with `equals` values `"pages-1"` resolved against `document.doc4ai.pages`. The Open WebUI shape (copied from `DoclingLoader`): `POST /v1/convert/file`, headers `X-Api-Key: <TOKEN>`, form `{"image_export_mode": "placeholder", "md_page_break_placeholder": "\f"}`, checks: `md_content` count `\f` equals `pages-1`, `md_content` not_contains `<!-- page:`, `status` equals `success`. RAGFlow v1 chunked: `POST /v1/convert/source`, `Authorization: Bearer <TOKEN>`, json `{"options": {"from_formats": ["pdf"], "to_formats": ["json", "md", "text"], "do_chunking": true, "chunking_options": {"max_tokens": 512, "overlap": 50, "tokenizer": "sentencepiece"}}, "sources": [{"kind": "file", "filename": "a.pdf", "base64_string": "<B64>"}]}`, checks: `document.text_content` not null, `document.json_content` is_null (or per spike S1), `document.doc4ai.ignored_options` list_contains `do_chunking`. RAGFlow v1alpha: path `/v1alpha/convert/source`, json `{"options": {...}, "file_sources": [{"filename": "a.pdf", "base64_string": "<B64>"}]}`. Each file's `"source"` field cites the URL and version the shape was copied from (spec §3).

- [ ] **Step 2: Run** `uv run pytest tests/compat/test_shapes.py -q` → FAIL until the shapes exist / pass once they do (RED is the missing files).
- [ ] **Step 3: Write the seven shape files**; fix any schema mismatch they reveal (e.g. `errors[].category` enum values, `task_position` nullability) in `render.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `test(compat): replay real client request shapes and validate against the docling-serve OpenAPI snapshot`.

---

### Task 12: Integration docs, README section, Kotaemon reference loader

**Files:**
- Create: `docs/integrations/open-webui.md`, `ragflow.md`, `kotaemon.md`, `dify.md`, `curl.md` (contents = spec §8.1–8.5, in Traditional Chinese, each with a 「可行性」 line A/B/C and a 「未驗證」 list)
- Create: `docs/integrations/kotaemon_doc4ai_loader.py` (~60 lines; `requests.post(f"{url}/v1/convert/file", files=…, headers={"X-Api-Key": key}, data={"md_page_break_placeholder": "\f"})` → per-page `Document(text, metadata={"page_label": i + 1, "file_name": name, "doc4ai": …})`)
- Modify: `README.md` — new section 「RAG platforms (docling-serve compatible API)」 after the MCP section: three lines + the Open WebUI env block (from `render_snippets`) + links to `docs/integrations/`
- Test: `tests/compat/test_docs.py` — the README contains the env block that `render_snippets` produces (same single source as the MCP section); every `docs/integrations/*.md` exists and names its 可行性 grade; the Kotaemon loader file compiles (`py_compile`) and its request shape equals `tests/compat/shapes/kotaemon_doc4ai_loader.json` (parse the two constants out of the file).

- [ ] **Step 1: Tests** (as described; assert the README section header and the `DOCLING_SERVER_URL=` line).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Write the docs** (spec §8 verbatim where applicable; keep the 「未驗證」 markers).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `docs(compat): integration recipes for Open WebUI, RAGFlow, Kotaemon, Dify and curl; README section`.

---

### Task 13: Real-client acceptance run and deviations

**Files:** Modify this plan (append results to "Implementation notes / deviations"); update `docs/integrations/*.md` 「未驗證」 lists with what was observed.

**Steps (manual; record pass/fail/not-run with versions):**

1. Start the server on the tailnet as today (`uv run aidoc serve --host 100.106.118.45 --port 3333 --token …`); create a PAT with `doc4ai:read`, `doc4ai:convert`.
2. **Open WebUI** (latest release, `docker run -p 3000:8080 ghcr.io/open-webui/open-webui:main` with `CONTENT_EXTRACTION_ENGINE=docling`, `DOCLING_SERVER_URL=http://ulove-arrangement.tail74077f.ts.net:3333`, `DOCLING_API_KEY=<PAT>`): upload (a) a Traditional-Chinese scanned PDF, (b) Head First Android (broken fonts), (c) a `.docx`. Expect: all three indexed; a question answered from (a) shows `(page N)` in the citation modal; Doc4AI Jobs shows `origin: compat`; 呼叫紀錄 shows `convert_file ok` with `python-requests` as client. Record the Open WebUI version, whether `\f` splitting produced page metadata, how many parallel uploads it issued (呼叫紀錄 timestamps).
3. **RAGFlow** 1.0 (Docker compose per its README; `DOCLING_SERVER_URL`, `DOCLING_API_KEY` in `.env`): knowledge base with PDF parser = Docling; upload one PDF; expect parsed chunks. Record the exact endpoints it called (呼叫紀錄: `convert_source` vs `v1alpha`, `protocol_version`) and whether `json_content: null` caused a retry or an error → update spec §8.2 U1 and, if needed, flip `compat.json_content` default (Task 1 S1 decision rule).
4. **curl async** against the live server (spec §8.5 commands).
5. **Slow tests**: `uv run pytest -m slow tests/compat` (the two §9.3 cases, implemented in Task 7 behind `@pytest.mark.slow` + `tests/manual.py` samples).
6. Record results, versions and every deviation below; commit `docs(plan): compat API acceptance results and deviations`.

---

## Phase acceptance

- [ ] `uv run pytest -m "not slow" -q` green (including `tests/compat/`); `ruff check src tests` no new findings.
- [ ] `pnpm --dir web test`, `pnpm --dir web build`, `pnpm --dir web e2e` green.
- [ ] Spec §1.4 criteria 1–5 verified (Task 13 rows 2–4; Task 6/11 for criterion 5; Task 4 for criterion 4).
- [ ] `tests/compat/shapes/` holds the seven shapes with source URLs + versions; the OpenAPI snapshot is committed with its version in the file name.
- [ ] README has the RAG platforms section; `docs/integrations/` has the five recipes with 可行性 grades.
- [ ] Spike file records S1–S5 with decisions; §3 D7/D8 of this plan reflect them.
- [ ] `aidoc.toml` has `[compat]`; schema v5 migration is idempotent on a v4 DB (run once against a copy of `data/aidoc.db`).

## Implementation notes / deviations

(Filled in while executing. Each entry: what changed vs this plan, why, the pinning test, the commit.)

Implemented 2026-10-10..12 on master (`668602b`..HEAD, pushed after each task), executed inline (superpowers:executing-plans).

**Spike (Task 1, `docs/superpowers/spikes/2026-10-compat.md`)**
- S1: RAGFlow 1.0.0-rc1 reads `md_content` first → D7 stays (`json_content: null`). New: its Docling parser uses a strict SSRF client that refuses private / loopback / CGNAT (Tailscale) hosts → the RAGFlow recipe needs a publicly routable Doc4AI URL. Its chunked attempts get no chunks and fall through; the third request (standard `/v1/convert/source`) is the one used.
- S2: extra keys are ignored by docling's models, but its enums bind our shapes: `ErrorItem.component_type` ∈ `pipeline|user_input|…`, `category` ∈ `FailureCategory` (A2's `"quality"` → `category: "unknown"`, `module_name: "doc4ai.quality"`); `task_meta` = `TaskProcessingMeta` (+ `pages_done`, `pages_total`); failures are `PublicFailureInfo` (the plan's `failure.error_message` is `failure.message`). D7a/D7b added to §3.
- S3: no container runtime running (Docker Desktop installed, daemon off, not started): the snapshot is `docling_serve.app.create_app().openapi()` of docling-serve 1.36.0 in a scratch venv.
- S4: Starlette's `form()` spools file parts without a cap → `python_multipart.MultipartParser` streaming with a byte cap.
- S5: Open WebUI processes each upload as its own request (40-thread pool) → keep `max_concurrent_jobs_per_token = 8`; sync requests wait for a slot.

**Contract changes / deviations (each pinned by a test)**
- Cache hit is by **sha256 across the library** (planned output dir first, then any visible document with that content), not by (sha, name-derived output dir): spec §4.3 step 2 / §8.1 say "同 sha", and Open WebUI uploads carry a per-upload UUID in the name (`test_cache_hit_makes_no_job`, renamed-file case). An explicit engine (`doc4ai_engine`, `force_ocr`) is not answered by another engine's result (`test_an_explicit_engine_is_not_answered_by_another_engines_cache`).
- Open WebUI's `<uuid>_` stored-name prefix is stripped from the client file name (`test_openwebui_uuid_prefixed_path_is_reduced_to_the_name`).
- "Converting right now" = any queued/running task with the same sha (`Store.live_task_for_sha`), checked together with job creation under one lock (`compat.jobs.admit`); `Store.busy_task_id` only sees live-locked tasks (`test_concurrent_same_file_waits_on_the_running_task`).
- Opaque task ids `t<uuid>` for async cache hits, immediate failures and attaching to another token's job (the plan suggested `c`+sha) (`test_async_same_file_running_returns_a_pollable_task`).
- **D6 amended:** a `low` task's `task_status` is `success` (docling-serve task states are pending|started|success|failure; Haystack polls until success|failure); the result's conversion status stays `partial_success` (`test_low_quality_task_is_success_and_its_result_partial_success`). `task_position`: the ahead-count while queued, 0 while running, null when finished.
- Compat jobs share the `mcp_jobs` per-token table with MCP jobs (compat limit 8); a job a request only attaches to is logged as `args.attached_job_id`, not `job_id`, so it uses none of the token's slots.
- Unknown `/v1/*`, `/v1alpha/*` paths answer docling-style `404 {"detail": "Not Found"}` (not the SPA); every HTTPException / validation error on those paths is `{"detail": …}`.
- `/api/mcp/calls` gained a `method` filter and 呼叫紀錄 a 「篩選來源」 select (spec §6; the plan's Task 10 omitted it). `render_snippets` has a fifth snippet `openwebui`; `readme_section` still renders the four MCP clients.
- The rate limit (spec §4.3 step 1) applies to the convert endpoints; a disk-space shortfall is `status: failure` `insufficient_disk`; async over the cap is `429`, logged `rate_limited/too_many_jobs`.
- Plan test corrections: `test_source_shape_and_v1alpha`'s first request is `cached: False` (the plan's `True` contradicted its own comment); the 504→poll tail of `test_sync_timeout_504_then_async_result` moved to `test_async.py::test_sync_timeout_then_poll_and_result` (the poll endpoint is Task 8's).
- 9 shape files (plan: 7): + `ragflow_1_0_v1_standard` (the request RAGFlow actually uses) and `haystack_url_source` (expected `422 unsupported_source: http`); the shapes use a 3-page PDF.
- Kotaemon graded **B** (spec: C): Kotaemon's `flowsettings.FILE_INDEX_PIPELINE_FILE_EXTRACTORS` maps an extension to a reader's dotted path and overrides the UI choice, so `docs/integrations/kotaemon_doc4ai_loader.py` drops in without a Kotaemon change (not run live).
- §9.3 slow tests live in `tests/integration/test_compat_real.py` (written in Task 13); the broken-font case sends `doc4ai_engine=docling` (auto routing OCRs that sample whole on MinerU, so there is no per-page repair to report).

**Final whole-branch review (fresh reviewer) — fix pass**, each with a test that failed first:
1. *Critical:* `image_export_mode=embedded` joined the image name from the Markdown onto `assets/`, so a document containing `![x](assets/../../../data/serve.token)` could leak any readable file (drive and UNC names too). Names now go through `safe_relative` and must resolve inside `assets/` (`test_embedded_mode_never_reads_outside_the_assets_dir`) — `6b32809`.
2. A literal `<!-- page: 2000000 -->` in the text sized the placeholder output; segments now come from `documents.pages` and out-of-range markers stay with the page they follow (`test_markers_past_the_page_count_do_not_create_segments`) — `6b32809`.
3. Attached jobs used the attaching token's job slots (`test_attaching_to_another_tokens_job_uses_none_of_my_slots`) — `eb05210`.
4. A multipart body without its closing boundary was converted and cached (`test_truncated_multipart_body_is_422_and_leaves_no_part`; also `test_oversized_upload_without_content_length_is_cut`) — `053997f`.
5. `force_ocr` / `doc4ai_engine` answered from another engine's cache — `be8928d`.
Deferred minors: blocking file I/O (multipart writes, sha256, base64 decode ≤ 50 MB) runs on the event loop; `task_position` uses `Store._q1`.

**Acceptance (2026-10-10..12)**
- Schema v5 on a copy of the live `data/aidoc.db` (v4, 68 documents, 57 jobs): v5, data intact, idempotent on a second open.
- Live server (`0.0.0.0:3333`, restarted on the new code) with a temporary PAT 「compat acceptance (temp)」 (`doc4ai:read`, `doc4ai:convert`): `/health`, `/v1/capabilities`, an Open WebUI-shaped sync convert (8-page PDF: `\f` × 7, no markers left, cache hit in 0.1 s on repeat), a curl-shaped async run (started → success, `task_meta` 3/3 pages, result success), bad token `401` + `WWW-Authenticate`, PAT on `/api` `401`, `?api_key=` `400`, server token on `/v1` `401`, call-log rows `compat/docling` with clients `python-requests` / `curl`, Jobs origin `compat`, `/api/mcp/status.compat`. The PAT was **revoked** afterwards (verified `401`).
- **Open WebUI 0.11.4** (pip in a scratch venv, no Docker; `CONTENT_EXTRACTION_ENGINE=docling`, `DOCLING_PARAMS={"doc4ai_lang": "cht"}`), driven through its REST API: `scanned_cht.pdf` (2 pages) → completed, vector metadata `page` 0–1; `hfad_garbled_mix.pdf` (9 pages, broken fonts) → completed in 77 s, `page` 0–8; `sample.docx` → completed, no page metadata. Doc4AI logged three `convert_file ok` from `python-requests` (`docling-serve-v1`), Jobs origin 「相容 API」. Chat answers with `(page N)` citations were not exercised (no LLM in the scratch instance). Scratch venv and data deleted.
- RAGFlow: **not run live** (needs Docker and a publicly routable Doc4AI URL, spike S1); its four request shapes are replayed in `tests/compat/shapes/`.
- Spec §1.4 (3), 300-page sync/async: covered by the 504 → poll → result tests with the fake engine; not run on a real 300-page file.
- Slow: `tests/integration/test_compat_real.py` 2 passed (MinerU for the Traditional Chinese scan, `\f` = pages − 1; broken-font pages reported `repaired`).
