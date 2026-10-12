# Doc4AI Studio

**Not sure whether to use MarkItDown, Docling or MinerU? Doc4AI picks one per document, checks the result page by page, repairs broken pages or switches engines when the output is wrong — and hands the Markdown to your AI through a web studio, a CLI and an MCP server.**

Doc4AI Studio is an orchestrator, not a new parser. It runs three existing open-source converters ([MarkItDown](https://github.com/microsoft/markitdown), [Docling](https://github.com/docling-project/docling), [MinerU](https://github.com/opendatalab/MinerU)) side by side in isolated environments, probes each input (text layer, image coverage, formulas, ruled tables, fonts without a Unicode map) to route it, scores the Markdown it gets back, and falls back to the next engine or repairs individual pages when the score says the conversion failed. The output is Markdown with `<!-- page: N -->` markers plus a JSON sidecar describing how it was made, ready for LLMs and RAG pipelines.

What exists today:

- **Multi-engine routing with fallback** — one engine per document, chosen from a probe of the file; a failed quality check (characters per page, garbage ratio, missing tables, page-map coverage) moves the document to the next engine.
- **Per-page quality and repair** — every page of a PDF is checked against its own text layer; pages with a broken text layer are re-run with OCR (Docling full-page RapidOCR, then MinerU OCR mode) and spliced back in place. Pages that cannot be repaired are flagged, not hidden.
- **Isolated engine environments** — each engine has its own `uv` venv under `envs/` and runs as a subprocess, so MinerU 4, Docling 2 and MarkItDown never fight over torch / onnxruntime versions.
- **Large documents** — PDFs over 40 pages are converted in 40-page segments with resume after a crash or cancel; tables that cross a segment boundary are joined. Tested on a 1,192-page book.
- **Traditional Chinese + English OCR**, including mixed pages.
- **Web studio** — resumable chunked uploads, live job progress (SSE), a library with quality badges, and a side-by-side view of the source and the Markdown with page-synced scrolling for PDFs. Word / PowerPoint / Excel / OpenDocument / RTF sources are shown as a PDF preview rendered by LibreOffice when it is installed (without scroll sync: MarkItDown output has no page markers).
- **CLI** — `aidoc convert`, `aidoc batch`, `aidoc chunk` (heading-aware RAG chunks with page ranges, tables never split), `aidoc serve`.
- **Claude Code skill** — [`skill/SKILL.md`](skill/SKILL.md).
- **MCP server** — Streamable HTTP at `/mcp` with personal access tokens (scopes, expiry, revoke, rotate, rate limits) and an admin dashboard showing tokens, connected clients and every call.
- **docling-serve compatible API** — `/v1/convert/*` on the same port, so Open WebUI (and other RAG platforms that can call a docling-serve server) use Doc4AI as their document parser, page numbers included.

## Screenshots

Document view — source PDF and Markdown side by side, scrolled in sync:

![Document view](docs/superpowers/screenshots/05-document-light.png)

A 315-page PDF converted in eight 40-page segments:

![Job detail with segments](docs/superpowers/screenshots/03-job-detail-light.png)

Library with engine and quality per document:

![Library](docs/superpowers/screenshots/04-library-light.png)

MCP admin — endpoint, live numbers and ready-to-paste client configuration:

![MCP overview](docs/superpowers/screenshots/10-mcp-overview-light.png)

## Requirements

- Python 3.10+ and [uv](https://docs.astral.sh/uv/)
- Node 20+ and pnpm (for the web studio)
- An NVIDIA GPU is strongly recommended for Docling / MinerU. Engine environments pin CUDA 12.8 PyTorch wheels (needed for RTX 50-series / Blackwell).
- Optional: [LibreOffice](https://www.libreoffice.org/) for the Office preview in the web studio (found on `PATH` or in the usual install folder; otherwise set `[preview] soffice_path` in `aidoc.toml`).
- Developed and tested on Windows 11; the code is cross-platform but other OSes are less tested. There is no Docker image yet.

## Quick start

The command-line tool is `aidoc`.

```bash
uv sync
uv run aidoc setup all          # creates the engine envs and downloads models (several GB)

uv run aidoc convert report.pdf -o out
uv run aidoc batch ./inbox -o out
uv run aidoc chunk out --max-tokens 800
```

Web studio:

```bash
pnpm --dir web install
pnpm --dir web build
uv run aidoc serve              # http://127.0.0.1:8765
```

By default the server binds to `127.0.0.1`. To expose it on your network, a token is required:
`uv run aidoc serve --host 0.0.0.0 --token <secret>`. While the server runs, `aidoc convert` and `aidoc batch` hand their work to its queue.

## VLM OCR (optional, off by default)

A fourth engine, `vlm`, sends page images to any **OpenAI-compatible vision endpoint** (Ollama or LM Studio on this
computer, vLLM / SGLang you host yourself, or a cloud API). It is only used where everything else failed: it is the
**last** engine of every PDF / image route (after MarkItDown) and the **last** per-page repair engine (after Docling
and MinerU). With `[vlm] backend = "none"` (the default) nothing changes.

```toml
[vlm]
backend = "openai"
base_url = "http://127.0.0.1:11434/v1"   # Ollama on this computer
model = "gemma4:12b"
remote_enabled = true                    # consent: page images go to base_url
allow_no_key = true                      # local servers need no key; otherwise set AIDOC_VLM_API_KEY
```

- **Privacy:** pages are sent only after `remote_enabled = true` (Settings → VLM OCR has the consent switch; an endpoint
  that is not on this computer is shown in red). Every use is logged (`vlm(openai): sending N page image(s) to <host>`)
  and recorded in the sidecar (`vlm.endpoint_host`, `vlm.pages_sent`). `aidoc convert --no-vlm-remote` turns it off for
  one run. Keep the key in the `AIDOC_VLM_API_KEY` environment variable; a key in `aidoc.toml` is masked by the API.
- **Limits:** a daily page budget (`remote_daily_page_budget`, default 500) and a per-document page cap
  (`remote_max_pages_per_doc`, default 200; per-page repair is limited by the budget only).
- `aidoc setup vlm` (or 「測試連線」 in Settings) sends one built-in test image and checks the answer.
- The VLM engine **does not extract images** (no layout boxes): pictures become `<!-- image missing -->`.
- The **local backend** (a llama.cpp server with PaddleOCR-VL-1.6 managed by aidoc) is **not available yet**: the
  feasibility spike did not pass its acceptance rule on this hardware (`docs/superpowers/spikes/2026-10-vlm-ocr.md`).

## Output

```
out/<name>/
├─ <name>.md      Markdown with <!-- page: N --> markers
├─ <name>.json    engine, attempts, probe results, quality score and per-page flags, timings
└─ assets/        extracted images
```

## MCP

Doc4AI Studio serves an MCP endpoint at `/mcp` (Streamable HTTP, Bearer personal access tokens). Create a token in the web UI (MCP → Tokens); it is shown once. Then connect a client:

**Claude Code**

```bash
claude mcp add --transport http doc4ai http://<host>:<port>/mcp --header "Authorization: Bearer <YOUR_TOKEN>"
```

**Cursor（~/.cursor/mcp.json）**

```json
{
  "mcpServers": {
    "doc4ai": {
      "url": "http://<host>:<port>/mcp",
      "headers": {
        "Authorization": "Bearer <YOUR_TOKEN>"
      }
    }
  }
}
```

**VS Code（.vscode/mcp.json）**

```json
{
  "servers": {
    "doc4ai": {
      "type": "http",
      "url": "http://<host>:<port>/mcp",
      "headers": {
        "Authorization": "Bearer ${input:doc4ai-token}"
      }
    }
  },
  "inputs": [
    {
      "id": "doc4ai-token",
      "type": "promptString",
      "password": true,
      "description": "Doc4AI Studio token"
    }
  ]
}
```

**Claude Desktop（claude_desktop_config.json，經 mcp-remote）**

```json
{
  "mcpServers": {
    "doc4ai": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "http://<host>:<port>/mcp",
        "--header",
        "Authorization:${DOC4AI_TOKEN}",
        "--allow-http"
      ],
      "env": {
        "DOC4AI_TOKEN": "Bearer <YOUR_TOKEN>"
      }
    }
  }
}
```

Tokens never go in the URL; the endpoint answers `401` with `WWW-Authenticate: Bearer realm="doc4ai"` when one is missing or revoked (after 10 failures a minute from one address: `429` with the same reason, for a minute). Scopes: `doc4ai:read`, `doc4ai:convert`, `doc4ai:convert:local`, `doc4ai:manage`.

MCP tools: `search_library`, `get_document_info`, `read_document` (page ranges; every response is capped at a token budget and continues with a cursor), `get_chunks`, `list_documents`, `convert_document`, `get_job`, `cancel_job`, `reconvert_document`, and `convert_path` (only when `[mcp] local_path_roots` is set). The web studio's MCP page lists tokens, connected clients and the call log; its Overview has a field where you can paste a token to fill the snippets above in the browser (the token is never sent or stored).

## RAG platforms (docling-serve compatible API)

The same server also speaks the [docling-serve](https://github.com/docling-project/docling-serve) v1 HTTP API (`/v1/convert/file`, `/v1/convert/source`, the async `…/async` + `/v1/status/poll` + `/v1/result`, `/health`), so RAG platforms whose document parser can be pointed at a docling-serve URL get Doc4AI's routing, per-page checks and OCR repair instead — with page numbers: the placeholder a client asks for (`md_page_break_placeholder`) goes between every two pages, empty pages included. Authentication is the MCP personal access token (`doc4ai:convert`) in `X-Api-Key` or `Authorization: Bearer`; every request shows up in the MCP page's call log, and jobs carry the source 「相容 API」. Already-converted content (same bytes, any file name) answers from the library at once.

Open WebUI (its "Docling" content extraction engine; the token dialog on the MCP page shows this block with the token filled in):

```bash
CONTENT_EXTRACTION_ENGINE=docling
DOCLING_SERVER_URL=http://<host>:<port>
DOCLING_API_KEY=<YOUR_TOKEN>
```

Recipes, with what is and is not verified: [Open WebUI](docs/integrations/open-webui.md) (works by configuration; tested with Open WebUI 0.11.4), [RAGFlow](docs/integrations/ragflow.md) (works, but RAGFlow 1.0 only connects to a publicly routable URL), [Kotaemon](docs/integrations/kotaemon.md) (a drop-in reader file), [Dify](docs/integrations/dify.md) (needs a Dify plugin, not written yet) and [curl / any docling-serve client](docs/integrations/curl.md). Not supported: server-side URL fetching (`sources[].kind = "http"`), docling chunking endpoints, `json_content` (always `null`).

## When to use something else

Doc4AI Studio is useful when you have a mixed pile of documents (scanned and born-digital PDFs, Office files, Traditional Chinese), want the conversion checked rather than trusted, and want to share the result with AI clients over a network. It is heavier than the alternatives below — several GB of models, uv + pnpm, no one-line install, no Docker image, no public benchmark yet — so a single tool is often the better choice:

| If you… | Consider |
|---|---|
| only need MinerU, and its own web UI, router and document library are enough | [MinerU](https://github.com/opendatalab/MinerU) 4.0 directly |
| need a Docling conversion API at scale, in containers | [docling-serve](https://github.com/docling-project/docling-serve) |
| want an agent to read local PDFs through a lightweight stdio MCP server with a one-line install | [anymd](https://github.com/SylphxAI/anymd) or [pdf-mcp](https://github.com/jztan/pdf-mcp) |
| only convert PDFs and want a library / CLI with per-page routing, or to audit another engine's output | [pdfmux](https://github.com/NameetP/pdfmux) |
| need a multi-user platform with RBAC, SSO and webhooks around MinerU | [MinerU Tianshu](https://github.com/magicyuan876/mineru-tianshu) |
| only convert Office / HTML files to Markdown | [MarkItDown](https://github.com/microsoft/markitdown) (and its `markitdown-mcp`) |
| want a parser inside a RAG platform | RAGFlow, Open WebUI and Dify already let you pick MinerU or Docling (Doc4AI can stand in for the Docling server: see above) |

## Engines and licenses

| Component | Role here | License |
|---|---|---|
| [MarkItDown](https://github.com/microsoft/markitdown) 0.1.8 | Office files, HTML, EPUB, CSV | MIT |
| [Docling](https://github.com/docling-project/docling) 2.x (+ RapidOCR / EasyOCR) | Born-digital PDFs (layout, tables, reading order); full-page OCR repair | MIT (OCR backends: Apache-2.0) |
| [MinerU](https://github.com/opendatalab/MinerU) 4.0.x | Scanned PDFs, images, formulas, complex layouts (GPU) | MinerU Open Source License (Apache-2.0 based, with additional conditions) |
| VLM OCR (optional) | Any OpenAI-compatible vision endpoint you configure: last fallback and last page repair | depends on the endpoint / model |
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) | Probing, splitting and checking PDFs in the main program | AGPL-3.0 |
| [LibreOffice](https://www.libreoffice.org/) (optional, external program) | Office → PDF preview in the web studio | MPL-2.0 |

The engines are separate projects installed into their own environments by `aidoc setup`; they are not redistributed with this repository. MinerU's license has additional conditions for large-scale or online-service use — check each project's license before redistributing Doc4AI Studio or offering it as a service.

## Tests

```bash
uv run pytest -m "not slow"     # fast suite, uses a fake engine (no GPU)
uv run pytest -m slow           # real engines on the GPU (and LibreOffice, when installed)
# VLM slow tests need an endpoint: AIDOC_VLM_REAL_BASE_URL=http://127.0.0.1:11434/v1 AIDOC_VLM_REAL_MODEL=gemma4:12b
pnpm --dir web test             # Vitest
pnpm --dir web e2e              # Playwright
```

## Docs

Design specs, implementation plans and research notes live in `docs/superpowers/`.

## 繁體中文

Doc4AI Studio 不是新的解析模型，而是編排層：不知道該用 MarkItDown、Docling 還是 MinerU 時，它依每份文件的特徵選引擎、逐頁檢查轉換結果，文字層壞掉的頁面用 OCR 修復後接回原位，整份不合格就換下一個引擎；產出帶頁碼標記的 Markdown 與 JSON 中繼資料。支援繁體中文與英文 OCR、上千頁文件分段轉換與續跑，並提供 Web 介面（原始檔與 Markdown 左右對照）、CLI、Claude Code skill，以及有 token 權限控管與管理後台的 MCP server；同一個 port 也提供 docling-serve 相容 API，Open WebUI 等 RAG 平台把 Docling 伺服器網址指過來就能用（頁碼保留）。如果只需要單一引擎，或只想要一行安裝的本機 MCP，上面「When to use something else」列的工具可能更適合。

## License

[AGPL-3.0](LICENSE). If you run a modified version as a network service, the AGPL requires you to offer its source to the users of that service. The engines keep their own licenses (see above).
