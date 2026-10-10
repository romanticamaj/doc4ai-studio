# MCP Distribution & Hardening — Implementation Plan (index)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement the sub-plans task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Read THIS file before any sub-plan: it holds the binding decisions, the contract amendments, the user-decision gates and the coverage table. Then read the spec and the two research documents named below.

**Goal:** Make the Phase 1 MCP server (a) installable — Docker images, `aidoc mcp install`, a Claude Desktop `.mcpb`, an MCP Registry entry; (b) reachable by cloud clients — public HTTPS + an embedded OAuth 2.1 authorization server so claude.ai / Claude Desktop connectors, Claude Code (OAuth) and ChatGPT can sign in; (c) safer — hidden/injected document text is detected at conversion time, stored per page, and every tool output fences document text and says so.

**Spec:** `docs/superpowers/specs/2026-10-05-mcp-distribution-hardening-design.md` (Traditional Chinese; APPROVAL PENDING — §11 lists the user decisions). Research: `docs/superpowers/research/2026-10-01-mcp-best-practices.md` (§2 OAuth, §3 security), `docs/superpowers/research/2026-10-02-similar-projects.md` (§7, §8.2). Binding contracts already in force: `plans/2026-09-24-index.md` (§0, §4, §7, §8), `plans/2026-10-01-mcp-server.md` (§0, §3, §4.1, §4.2, §6 hooks), `plans/2026-10-05-docling-compat-api.md` (§4: schema v5, `/health`) — amended, additively only, in §4 of this file.

**Sub-plans (recommended order; each is independently shippable):**

| Part | File | Tasks | Delivers | Gate |
|---|---|---|---|---|
| 4c — Untrusted content | `docs/superpowers/plans/2026-10-05-mcp-distribution-4c-untrusted-content.md` | C1–C9 | `aidoc/hidden.py` detector (PDF geometry, Office XML, Unicode, injection patterns), per-page flags in `quality.pages[]` + `quality.untrusted`, reassess of existing documents, fenced tool outputs with notice + nonce boundary, flags in search/list/info, compat `untrusted_pages`, Library badge + Document page warnings | none |
| 4a — Packaging | `docs/superpowers/plans/2026-10-05-mcp-distribution-4a-packaging.md` | A1–A11 | engine envs with `cpu`/`cu128` dependency groups, `aidoc setup --no-sync`, Dockerfile (cpu + cuda targets), entrypoint, compose, docs, CI image build, `aidoc mcp install`, install links, `.mcpb`, `server.json` + release doc | D4/D5/D6 for the publish steps only |
| 4b — OAuth 2.1 | `docs/superpowers/plans/2026-10-05-mcp-distribution-4b-oauth.md` | B1–B14 | `public_url`/proxy trust, PRM + AS metadata, embedded AS (CIMD, DCR, PKCE, refresh rotation, revoke), verifier chain (`OAuthTokenVerifier`, `JwtVerifier`), consent screen, admin API + OAuth tab, security suite, real-client acceptance | **D1/D2/D3** before Task B2 |

Each sub-plan ends with a **Phase acceptance** checklist; the project is done when §8 of this file passes.

---

## 0. Global constraints (every task of every sub-plan)

MCP plan §0 applies unchanged (SDK pin, Streamable HTTP at `/mcp`, PAT rules, logging rules, budgets, both protocol eras, Windows host, test commands). In addition:

- **Phase 1 is frozen on the outside**: tool names, existing output fields, PAT format, `/api/mcp/*` response shapes only gain fields. Every sub-plan runs the whole `tests/mcp` suite before each commit.
- **One `Principal`.** OAuth adds verifiers, never a second permission model. `Principal` gains no fields; `kind="oauth"` uses `client_id`, `subject="owner"`, `token_id` = the access-token row id.
- **Schema v6** (4b) and **v7** (none planned — 4c stores inside `quality_json`): only new tables; never `ALTER` an existing one (the project rule since MCP plan A-M1). 4c adds no tables.
- **Secrets never land in logs**: `aidoc.mcp.redact` is extended for every new secret prefix (`doc4ai_at_`, `doc4ai_rt_`, `doc4ai_ac_`, `doc4ai_cl_`) and for `code=` / `refresh_token=` / `code_verifier=` form values **before** the endpoint that produces them is written (4b Task B4 precedes B6).
- **Docker is a distribution format, not the dev loop.** Nothing in `src/aidoc` may require running inside a container; every container path is an existing `AIDOC_*` environment variable.
- **Untrusted-content notices are descriptive.** Tool outputs state what the text is and where it came from; they never contain imperative instructions to the model beyond the one fixed sentence in spec §9.6 (Anthropic guidance: instructions inside tool results are ignored or flagged).
- **Public exposure is opt-in and gated** (§7 of this file): no task may enable `oauth.enabled`, start a Funnel, publish a Registry entry or push a public image without the user's decision recorded in the sub-plan's deviations section.
- Concurrency note: `src/aidoc/server/app.py`, `src/aidoc/config.py`, `aidoc.toml`, `src/aidoc/server/api/settings.py`, `src/aidoc/pipeline.py`, `src/aidoc/quality.py` are touched by other in-flight work (Office preview, VLM OCR engine, verify/benchmark, compat API). Before editing any of them: `git pull --rebase`, re-read, keep edits to the lines named in the task, add files by name (never `git add -A`).
- Commit trailer (both lines at the end of every commit message):
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0175gLq9uam6M5Z9yNNxM4Ba
  ```

## 1. Review focus (cross-plan input classes)

1. **A document that is both repaired and hidden-text-flagged on the same page** (4c): `quality.pages[]` entries keep one record per page with reasons from both families; `page_warnings` and the Library badge show both; the level stays what the quality rules say. → 4c Task C3 `test_page_record_merges_quality_and_security_reasons`.
2. **An OCR'd scan (Tr 3 everywhere, GlyphLessFont)** must produce `ocr_layer` on every page and zero `hidden_*`; the notice says "scanned document with an OCR text layer", not "hidden text". → C2 `test_ocr_sandwich_is_not_hidden`.
3. **A token that is valid for PAT but presented as `X-Api-Key` to `/oauth/token`**: the OAuth endpoints never accept PATs; `invalid_client` / `invalid_grant` with no hint that the string was a PAT. → B6 `test_pat_is_not_an_oauth_credential`.
4. **`public_url` with a path prefix** (`https://x.example/doc4ai`): PRM at `/.well-known/oauth-protected-resource/doc4ai/mcp`, canonical resource `https://x.example/doc4ai/mcp`, metadata endpoints under the prefix, `root_path` set. → B2 `test_public_url_with_prefix`.
5. **Docker image started without `AIDOC_TOKEN` but bound to 127.0.0.1 only (`--network host` dev use)**: allowed; the refusal applies only to non-loopback binds, same as today. → A4 `test_entrypoint_loopback_without_token_is_allowed`.
6. **`aidoc mcp install --client cursor` when `~/.cursor/mcp.json` has a trailing comma / comments (JSON5)**: refuse (exit 2) with the parse position; never rewrite. → A7 `test_broken_json_is_never_overwritten`.

## 2. File structure (new and modified; one responsibility per file)

```
# 4c
src/aidoc/hidden.py                         C1 spike probes; C2 PDF detector; C4 Office/Unicode/patterns
src/aidoc/models.py                         C3 QualityResult.untrusted (dict | None)
src/aidoc/quality.py                        C3 merge_untrusted(pages, findings), UNTRUSTED_VERSION
src/aidoc/pipeline.py                       C3 hidden.scan() between check_document and _write_output
src/aidoc/reassess.py                       C5 rerun when untrusted.version missing/old
src/aidoc/store.py                          C5 list_documents(flag="untrusted")
src/aidoc/mcp/schemas.py                    C6 Untrusted*, content_notice, untrusted fields
src/aidoc/mcp/registry.py                   C6 fenced text rendering with nonce; notice
src/aidoc/mcp/docs.py                       C6 untrusted_summary(row), page_notice(row, page)
src/aidoc/mcp/tools_read.py                 C6 fields on search/list/info/read/chunks
src/aidoc/mcp/resources.py                  C6 notice on doc4ai:// reads
src/aidoc/mcp/server.py                     C6 INSTRUCTIONS sentence about `untrusted`
src/aidoc/compat/render.py                  C7 untrusted_pages in doc4ai block (if compat plan landed; else skip)
web/src/features/library/badges.ts, PageBadges.tsx   C8
web/src/features/document/pageWarnings.ts   C8 untrusted spans in the warning row
tests/fixtures/make_fixtures.py             C2/C4 hidden-text fixtures
tests/unit/test_hidden.py, tests/unit/test_hidden_office.py, tests/mcp/test_untrusted_output.py, tests/reliability/test_untrusted_pipeline.py
docs/superpowers/spikes/2026-10-untrusted.md   C1

# 4a
envs/{docling,mineru}/pyproject.toml, uv.lock   A1/A2 dependency groups cpu | cu128
src/aidoc/config.py                         A2 Engines.torch_variant
src/aidoc/setup_engines.py                  A2 --group, --no-sync, cpu self-check rule
src/aidoc/cli.py                            A2 setup flags; A6 `aidoc mcp install`
docker/Dockerfile, docker/entrypoint.sh, compose.yaml, compose.cpu.yaml, .env.example, .dockerignore   A3/A4
docs/deploy/docker.md                       A5
.github/workflows/docker.yml                A5 (cpu build + test; cuda build only)
src/aidoc/mcp/snippets.py                   A6 client_configs(), install links
src/aidoc/mcp/install.py                    A6/A7 writers per client
src/aidoc/server/api/mcp.py                 A8 install_links in config-snippets
web/src/features/mcp/ConfigSnippets.tsx     A8 install buttons
mcpb/manifest.json, mcpb/server/index.js, mcpb/package.json, mcpb/icon.png   A9
server.json, docs/release/registry.md       A10
tests/unit/test_setup_variants.py, tests/unit/test_mcp_install.py, tests/unit/test_dockerfiles.py, tests/mcp/test_install_links.py, tests/unit/test_server_json.py, tests/mcp/test_mcpb.py
docs/superpowers/spikes/2026-10-packaging.md   A1

# 4b
src/aidoc/config.py                         B2 Server.public_url, trusted_proxies; Oauth, OauthExternal
src/aidoc/cli.py                            B2 --public-url; uvicorn proxy flags
src/aidoc/mcp/server.py                     B2 canonical_resource(), public hosts in allowed_hosts/endpoint_urls
src/aidoc/oauth/__init__.py                 B3 package
src/aidoc/oauth/metadata.py                 B3 PRM + AS metadata routers
src/aidoc/store.py                          B3 schema v6 tables + methods
src/aidoc/mcp/redact.py                     B4 new prefixes and form fields
src/aidoc/oauth/tokens.py                   B4 prefixes, issue/hash helpers (reuse aidoc.mcp.tokens)
src/aidoc/oauth/clients.py                  B5 CIMD fetcher (SSRF guard, cache), DCR, manual
src/aidoc/oauth/grants.py                   B6 codes, grants, access/refresh issue + rotation + revoke
src/aidoc/oauth/endpoints.py                B6 /oauth/token, /oauth/register, /oauth/revoke; B7 /oauth/authorize + consent HTML
src/aidoc/oauth/consent.py                  B7 nonce, cookie, templates
src/aidoc/mcp/principal.py                  B8 OAuthTokenVerifier, JwtVerifier, VerifierChain
src/aidoc/mcp/gate.py                       B8 chain + resource_metadata on 401 when enabled
src/aidoc/compat/auth.py                    B8 same chain (if compat landed)
src/aidoc/mcp/registry.py                   B8 _meta mcp/www_authenticate on forbidden_scope for oauth principals
src/aidoc/mcp/calllog.py                    B9 oauth client identity in mcp_clients
src/aidoc/server/api/mcp.py                 B10 /api/mcp/oauth/*
web/src/features/mcp/OAuthTab.tsx (+ api, types, nav)   B11
src/aidoc/server/maintenance.py             B6 prune codes/tokens/clients
tests/oauth/ (conftest with a local CIMD document and a test IdP JWKS), tests/mcp parametrised by oauth.enabled
docs/deploy/https.md                        B2
docs/superpowers/spikes/2026-10-oauth.md    B1
```

## 3. Binding decisions

- **D1 Order and independence.** 4c first (no external dependency, immediate safety win), then 4a, then 4b. 4a/4b/4c touch disjoint modules except `config.py`, `app.py`, `aidoc.toml`, `redact.py`; each sub-plan adds its own config section and its own passthrough entries.
- **D2 Engine variant switch** lives in the env projects as uv dependency groups `cpu` and `cu128` (spec §3.1); `[engines] torch_variant` chooses at `aidoc setup`; the Windows host default stays `cu128`; `markitdown` has no torch and ignores the variant. If spike A1 shows group-scoped `tool.uv.sources` cannot express the two indexes in one lock, fall back to **two lock files** (`uv.lock` for cu128, `uv.cpu.lock` for cpu, generated with `UV_LOCK_FILE`/`--lockfile` if available, else by swapping the index URL in a temp copy during the Docker build) — the spike records which.
- **D3 Models never in the image**; `.ready` markers live in the container layer and are recomputed by `aidoc setup all --no-sync` in the entrypoint (models present → seconds). `AIDOC_SETUP_ENGINES` limits which engines are prepared.
- **D4 `aidoc mcp install` writes files; it never stores tokens itself.** Config-file writers are pure functions `(existing: dict | None, cfg: ClientConfig) -> dict` tested without touching the real home directory (`AIDOC_HOME_OVERRIDE` env for tests). Claude Code is driven through the `claude` CLI, not by editing `~/.claude.json`.
- **D5 `.mcpb` is a Node bundle around `mcp-remote`**; its manifest is generated from `aidoc.__version__` + the tool list by `scripts/build_mcpb.py` so the three sources (manifest, `server.json`, README) cannot drift.
- **D6 Embedded AS, opaque tokens, same HMAC scheme as PATs** (`tokens.py` reused with new prefixes); external-IdP mode is RS-only through `JwtVerifier`. No Authlib, no python-sdk `auth_server_provider`.
- **D7 Consent credential = server token**, with a 12-hour "remember this browser" cookie signed with `HMAC(server_secret, token_hash + cookie_id)`, so changing the server token invalidates every cookie.
- **D8 Verifier chain by prefix**: `doc4ai_pat_` → `PatVerifier`; `doc4ai_at_` → `OAuthTokenVerifier`; otherwise `JwtVerifier` only when `oauth.mode == "external"`; anything else → `bad_format`. The chain lives in `principal.VerifierChain` and is what `McpGate` / compat / resources call; the single `verify(raw) -> Principal | AuthFailure` signature is unchanged.
- **D9 401 shape**: `resource_metadata` is appended **only when** `oauth.enabled`; the Phase 1 shape is otherwise byte-identical (spike S13 behaviour preserved for PAT-only deployments).
- **D10 Hidden-text geometry source** is `page.get_texttrace()` (PyMuPDF ≥ 1.28, already installed) with `rawdict` only for the clip (Tr 7) check; per-page cost is bounded by skipping pages whose text-layer character count is 0 (pure scans → only the OCR-layer rule).
- **D11 Flags live in `quality`** (`quality.pages[].reasons` + `quality.untrusted`), so the sidecar, `documents.quality_json`, `/api/documents`, MCP `get_document_info` and compat all see them through existing plumbing; `UNTRUSTED_VERSION = 1` in `quality.py` drives reassessment.
- **D12 Fencing format** (spec §9.6): text block = existing header comment + ` untrusted=<none|info|high> boundary=<nonce>` + `\n<<<doc4ai-content <nonce>>>>\n` … `\n<<<end doc4ai-content <nonce>>>>`; `structuredContent.content_notice` fixed sentence; flagged pages get one `<!-- doc4ai: page N … -->` line before their block. Budget accounting: the nonce lines are part of `TEXT_RESERVE_TOKENS` (raised from 100 to 140); per-page notice lines are counted into that page's unit.

## 4. Contract amendments (additive)

**Schema v6 (4b, `SCHEMA_VERSION = 6`)**: tables `oauth_client_meta`, `oauth_codes`, `oauth_grants`, `oauth_tokens` (+ index), `oauth_consents` exactly as spec §5.6; `oauth_clients` (reserved since v4) starts being written. Store methods (names fixed):

```python
# clients
upsert_oauth_client(*, client_id, kind, name, redirect_uris: list[str], metadata_url=None, client_uri=None, logo_uri=None,
                    software_id=None, software_version=None, metadata_json=None, cache_until=None) -> None
get_oauth_client(client_id) -> dict | None            # joined with oauth_client_meta; row["redirect_uris"] parsed
list_oauth_clients() -> list[dict];  set_oauth_client_disabled(client_id, at: float | None)
touch_oauth_client(client_id, now);  prune_oauth_clients(*, dcr_unused_before: float) -> int
# codes / grants / tokens
create_oauth_code(*, code_hash, client_id, redirect_uri, code_challenge, scopes, resource, subject, expires_at) -> None
take_oauth_code(code_hash) -> dict | None            # atomic read+delete (BEGIN IMMEDIATE)
create_oauth_grant(*, client_id, subject, scopes, resource, created_ip) -> str
get_oauth_grant(grant_id);  list_oauth_grants(include_revoked=False);  revoke_oauth_grant(grant_id, reason, at) -> int  # tokens revoked
create_oauth_token(*, grant_id, kind, token_hash, prefix, expires_at, parent_id=None) -> str
get_oauth_token_by_hash(token_hash) -> dict | None   # joined with grant (grant_revoked_at, scopes, resource, client_id, subject)
rotate_oauth_refresh(old_id, *, new_hash, new_prefix, expires_at, at) -> str   # marks old rotated_at, inserts child
touch_oauth_token(token_id, now);  prune_oauth(*, now) -> dict
# consents
create_oauth_consent(*, cookie_hash, expires_at, ip) -> str;  get_oauth_consent_by_hash(h);  delete_oauth_consent(id);  list_oauth_consents()
```

**Config (§7 of index)**: `[server] public_url`, `trusted_proxies`; `[engines] torch_variant`; `[oauth]` + `[oauth.external]`; `[untrusted]` — exactly spec §10. `PUT /settings` whitelist: `[oauth]` all keys (`enabled=true` refused with 422 `public_url_required` when `server.public_url` is not `https://`), `[untrusted]` all keys; `server.public_url` / `trusted_proxies` / `engines.torch_variant` read-only (403 `server_readonly` / `engines_readonly`).

**API (`/api/mcp/*`)**: `GET /mcp/status` gains `oauth: {enabled, mode, issuer, canonical_resource}` and `public_url`; `GET /mcp/config-snippets` gains `install_links: [{client, url}]` and the `claude.ai` / `chatgpt` text snippets; new `/api/mcp/oauth/*` per spec §7.2. SSE: `mcp.oauth` event per spec §7.2; `mcp.call` gains `untrusted: bool`.

**MCP tool outputs (4c)**: `ReadOut`, `ChunksOut`, `SearchOut`, `DocInfoOut`, `DocSummary` gain the optional fields in spec §9.6 (`content_notice`, `untrusted`, `untrusted: bool` on hits/summaries). `TEXT_RESERVE_TOKENS = 140`. `INSTRUCTIONS` gains one sentence.

**Quality JSON (4c)**: `quality.untrusted = {version: 1, pages: [int], hidden_chars: int, levels: {high: n, info: n}, spans_truncated: bool}`; page records may carry `spans: [{reason, text, bbox?, size?, opacity?, font?}]` and `injection_hits: [str]`; new page reason codes per spec §9.3 (`hidden_invisible`, `hidden_tiny`, `hidden_transparent`, `hidden_lowcontrast`, `hidden_offpage`, `hidden_clipped`, `hidden_unicode`, `hidden_vanish`, `hidden_sheet`, `hidden_rowcol`, `hidden_offslide`, `ocr_layer`, `speaker_notes`, `injection_pattern`). `doc_flags()` adds `untrusted_content` (any high) and `untrusted_info`.

**Compat (if landed)**: `document.doc4ai.untrusted_pages` filled from `quality.untrusted.pages`.

## 5. Spec ambiguities resolved here

| # | Ambiguity | Resolution |
|---|---|---|
| M1 | Spec §5.6 says `mcp_clients` keyed by `(token_id, client_name, client_version)` with `token_id NULL` for OAuth — SQLite UNIQUE treats NULLs as distinct, so OAuth clients would duplicate. | Store `token_id = "oauth:" + oauth_client_id` for OAuth rows (string, never NULL) and keep `oauth_client_id` filled; `/api/mcp/clients` and events resolve the display name from the prefix. No schema change. |
| M2 | Spec §9.6 "hidden text stays in the Markdown" vs clients that want it removed. | Stays; a future `read_document(..., hidden_text="omit")` is reserved as an optional parameter name, not implemented. |
| M3 | Where `untrusted` goes when a document is reconverted and the new output has none. | `quality.untrusted` is always written (possibly `pages: []`), so the badge clears on reconvert. |
| M4 | `aidoc mcp install --client all` when one client fails. | Continue with the others; exit code is the highest failure code; summary table printed. |
| M5 | Docker `cuda` image on a host without a GPU. | Starts (torch cu128 wheels run on CPU); `/api/system` shows `cuda: false`; entrypoint prints a warning. Not an error. |
| M6 | OAuth scopes requested but not granted at consent. | The grant holds the consented subset; the token response's `scope` reflects it (RFC 6749 §5.1), and the client's later `insufficient_scope` is the normal step-up path. |
| M7 | `code_verifier` length/charset. | Enforce RFC 7636: 43–128 chars of `[A-Za-z0-9-._~]`; else `invalid_grant`. |
| M8 | Which `Host` the consent form posts to behind a proxy. | Forms use relative action URLs; redirects use `public_url`. |

## 6. Coverage table (spec section → sub-plan task)

| Spec | Requirement | Task(s) |
|---|---|---|
| §1.4 (1) compose up, engines ready, models on volume | | A3, A4, A5 |
| §1.4 (2) `aidoc mcp install` + `claude mcp list` | | A6, A7, A11 (manual) |
| §1.4 (3) `.mcpb` works in Claude Desktop | | A9, A11 |
| §1.4 (4) `server.json` validates | | A10 |
| §1.4 (5) claude.ai CIMD flow + revoke | | B5, B6, B7, B14 |
| §1.4 (6) ChatGPT / Inspector | | B14 |
| §1.4 (7) Claude Code OAuth loopback | | B6 (loopback rule), B14 |
| §1.4 (8) security suite | | B12 |
| §1.4 (9) PAT regression | | B8, B12 |
| §1.4 (10) fixtures flagged, OCR not | | C2, C4 |
| §1.4 (11) fenced output, flags, ≤ 40 tokens | | C6 |
| §1.4 (12) reassess + badge | | C5, C8 |
| §3.1 Docker design (targets, groups, volumes, entrypoint, healthcheck, labels, compose, Windows doc, CI) | | A1–A5 |
| §3.2 `aidoc mcp install`, install links, single source | | A6, A7, A8 |
| §3.3 `.mcpb` | | A9 |
| §3.4 Registry | | A10 |
| §4 public HTTPS, `public_url`, proxy trust, canonical URL, docs | | B1, B2 |
| §5.1 client requirements | | B3, B5, B6 (each row has a test) |
| §5.2 AS choice | | D6 |
| §5.3 endpoints | | B3, B6, B7 |
| §5.4 CIMD / DCR / manual | | B5 |
| §5.5 code flow rules 1–7 | | B6 |
| §5.6 schema v6 | | B3 |
| §6 verifier chain, step-up `_meta`, redaction | | B4, B8 |
| §7.1 consent | | B7 |
| §7.2 admin API + tab, SSE | | B10, B11 |
| §7.3 lifetimes | | B6 (config), B12 |
| §8 security checklist | | B12 (one test per row) |
| §9.2 principles, §9.3 detector table, §9.4 patterns | | C1, C2, C4 |
| §9.5 data flow, reassess | | C3, C5 |
| §9.6 outputs | | C6, C7 |
| §9.7 web | | C8 |
| §9.8 fixtures | | C2, C4, C9 |
| §10 config | | A2, B2, C3 |
| §11 user decisions | | §7 below |
| §12.2 spikes | | A1, B1, C1 |

## 7. User-decision gates

| Gate | Spec | Needed before | Default if the user says "go with the recommendation" |
|---|---|---|---|
| G1 Expose publicly? How? | D1, D2 | 4b Task B2 (`public_url` can be set to a tailnet HTTPS URL for tests only; Funnel enabled only after G1) | Tailscale Funnel on 443, enabled only while needed |
| G2 Consent credential = server token | D3 | 4b Task B7 | yes |
| G3 Registry namespace / account; `remotes[]` | D4 | 4a Task A10 publish step | `io.github.romanticamaj/doc4ai`; no `remotes[]` |
| G4 GHCR image name / public | D5 | 4a Task A5 push step | `ghcr.io/romanticamaj/doc4ai-studio`, public |
| G5 `.mcpb` signing | D6 | 4a Task A9 sign step | self-signed |
| G6 Show visible injection patterns as info | D7 | 4c Task C6 | yes |
| G7 ChatGPT acceptance method | D8 | 4b Task B14 | MCP Inspector if no developer mode |

Record each answer (date, choice) in the relevant sub-plan's deviations section before executing the gated step.

## 8. Definition of done

1. All three sub-plan acceptance checklists pass on the Windows host (4a's container checks on the Windows host via Docker Desktop WSL 2, plus one Linux run if available).
2. `uv run pytest -m "not slow"` green (including `tests/oauth/`, `tests/unit/test_hidden*.py`); `pnpm --dir web test`, `build`, `e2e` green.
3. Spike files `2026-10-untrusted.md`, `2026-10-packaging.md`, `2026-10-oauth.md` record every question's finding and decision.
4. Gates G1–G7 answered and recorded; gated steps executed or explicitly skipped.
5. README documents Docker, `aidoc mcp install`, `.mcpb`, the Registry entry, OAuth connector setup (claude.ai / ChatGPT) and the untrusted-content notice.
