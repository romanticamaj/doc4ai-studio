# Changelog

Notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-11

First public release.

### Added

- Routing across MarkItDown, Docling and MinerU, chosen from a probe of each document, with fallback to the next engine when the quality check fails
- Page-by-page quality checks, OCR repair of broken pages, and flags for pages that cannot be repaired
- Optional VLM OCR, off by default
- Isolated `uv` environment for each engine
- Segmented conversion of large PDFs with resume after a crash or cancel
- Web studio with resumable uploads, live progress, a library with quality badges, and a side-by-side source and Markdown view
- CLI: `doc4ai convert`, `batch`, `chunk` and `serve`
- MCP server at `/mcp` with personal access tokens, an admin dashboard and browser authorization through OAuth
- docling-serve compatible API at `/v1/convert/*`
- Claude Code skill

### Changed

- The project was renamed from `aidoc` to `doc4ai`. The `aidoc` command, `AIDOC_*` environment variables and an existing `aidoc.toml` keep working. Stored data keeps its old names, so existing libraries need no migration.

[Unreleased]: https://github.com/romanticamaj/doc4ai-studio/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/romanticamaj/doc4ai-studio/releases/tag/v0.1.0
