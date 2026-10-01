# Doc4AI Studio

**AI-friendly document conversion.**

Doc4AI Studio converts almost any document (PDF, scanned PDF, images, Word, PowerPoint, Excel, HTML, EPUB…) into clean **Markdown + JSON metadata** that LLMs and RAG pipelines can actually use. It picks the right engine per file, checks the output quality, and falls back automatically when a conversion looks wrong.

把各種文件轉成適合 AI 讀取的 Markdown 與中繼資料；支援繁體中文與英文 OCR。

## Engines

| Engine | Used for |
|---|---|
| [MarkItDown](https://github.com/microsoft/markitdown) | Office files, HTML, EPUB, CSV |
| [Docling](https://github.com/docling-project/docling) | Born-digital PDFs (layout, tables, reading order) |
| [MinerU](https://github.com/opendatalab/mineru) | Scanned PDFs, images, formulas, complex layouts (GPU) |

Each engine runs in its own isolated `uv` environment (`envs/<engine>/`) and is called as a subprocess, so they can be upgraded independently.

## Features

- **Routing + quality fallback** — probes each file (text layer, image coverage, formulas, tables) to choose an engine, scores the result (characters per page, garbage ratio, missing tables) and retries with the next engine when it fails.
- **Traditional Chinese + English OCR**, including mixed-language pages.
- **Large documents** — PDFs over 40 pages are converted in segments with resume after interruption; cross-segment tables are joined.
- **RAG chunking** — `aidoc chunk` splits by heading hierarchy with page ranges, never splitting tables.
- **Web app** — upload (chunked, resumable), live job progress over SSE, library, side-by-side PDF / Markdown view with page-synced scrolling, chunk preview, engine setup. Light and dark themes.
- **Reliability** — SQLite job state, crash recovery, atomic output writes, source-change detection.
- **Claude Code skill** — `skill/SKILL.md`.

## Requirements

- Python 3.10+ and [uv](https://docs.astral.sh/uv/)
- Node 20+ and pnpm (for the web app)
- An NVIDIA GPU is strongly recommended for Docling / MinerU. Engine environments pin CUDA 12.8 PyTorch wheels (needed for RTX 50-series / Blackwell).
- Developed and tested on Windows 11; the code is cross-platform but other OSes are less tested.

## Quick start

The command-line tool is `aidoc`.

```bash
uv sync
uv run aidoc setup all          # creates the engine envs and downloads models (several GB)

uv run aidoc convert report.pdf -o out
uv run aidoc batch ./inbox -o out
uv run aidoc chunk out --max-tokens 800
```

Web app:

```bash
pnpm --dir web install
pnpm --dir web build
uv run aidoc serve              # http://127.0.0.1:8765
```

By default the server binds to `127.0.0.1`. To expose it on your network, a token is required:
`uv run aidoc serve --host 0.0.0.0 --token <secret>`.

## Output

```
out/<name>/
├─ <name>.md      Markdown with <!-- page: N --> markers
├─ <name>.json    engine, attempts, probe results, quality score, timings
└─ assets/        extracted images
```

## Tests

```bash
uv run pytest -m "not slow"     # fast suite, uses a fake engine (no GPU)
uv run pytest -m slow           # real engines on the GPU
pnpm --dir web test             # Vitest
pnpm --dir web e2e              # Playwright
```

## Docs

Design spec and implementation plans live in `docs/superpowers/`.

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

## License

[AGPL-3.0](LICENSE). The engines are separate projects with their own licenses — notably MinerU's license has additional conditions for large-scale or online-service use; check each project before redistributing or offering this as a service.
