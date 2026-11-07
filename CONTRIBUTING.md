# Contributing to Doc4AI Studio

Bug reports, sample-based conversion problems, docs fixes and code are all welcome. For a larger change, open an issue first so we can agree on the approach before you spend time on it.

## Set up

You need Python 3.10+, [uv](https://docs.astral.sh/uv/), Node 20+ and pnpm. The fast tests use a fake engine, so you do not need a GPU or the engine environments.

```bash
git clone https://github.com/romanticamaj/doc4ai-studio.git
cd doc4ai-studio
uv sync
pnpm --dir web install
```

## Run the checks

```bash
uv run ruff check src tests bench
uv run pytest                   # fast suite, no GPU
pnpm --dir web exec tsc -b --noEmit
pnpm --dir web test             # Vitest
pnpm --dir web e2e              # Playwright
```

`uv run pytest -m slow` runs the real engines and needs them installed plus a GPU. CI does not run it, so if you change an engine or the routing, run it locally and say so in the pull request.

## How the code is laid out

| Path | What it holds |
|---|---|
| `src/doc4ai/` | Pipeline, engines, quality checks, server, MCP, OAuth, CLI |
| `envs/` | One isolated `uv` environment per engine |
| `web/` | React web studio |
| `bench/` | Benchmark and evaluation scripts |
| `tests/` | `unit`, `api`, `integration`, `mcp`, `oauth`, `compat`, `reliability` |
| `docs/integrations/` | Guides for RAG platforms |

## What we ask of a change

- **Fix the cause.** A conversion bug usually belongs in a probe, a quality check or the routing, so that it stops for every document of that kind.
- **Never hide a failure.** A page that cannot be repaired is flagged. A silent fallback, or a skipped test on a real document, does not count as passing.
- **Add a test** that fails without your change.
- **Keep persisted names stable.** The database file `aidoc.db`, the `aidoc_version` field and the output format value `aidoc` come from the project's old name. Existing data depends on them.
- **Keep private things out.** No real documents, tokens, host names or IP addresses in commits, fixtures or screenshots.

## Commits and pull requests

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/), for example `fix(oauth): ...` or `docs(readme): ...`. Keep each pull request to one change.

By contributing you agree that your work is licensed under [AGPL-3.0](LICENSE). This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
