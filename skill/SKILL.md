---
name: aidoc-convert
description: Convert a document (PDF, scanned PDF, image, docx/pptx/xlsx, html, epub...) into AI-friendly Markdown with the aidoc CLI. Use when the user says 「轉成 markdown」「把這份文件轉給 AI 讀」「convert this doc」「幫我讀這個 PDF」 or asks to read/summarise a non-text file.
---

# aidoc convert

All routing/engine decisions live in the CLI. This skill only calls it and reads the result.

## Steps
1. Resolve the file path the user means. If it does not exist, ask.
2. Run (from the ai-friendly-doc checkout, or with `AIDOC_ROOT` pointing to it):
   `uv run --project <AIDOC_ROOT> aidoc convert "<file>" -o "<AIDOC_ROOT>/out" --json`
   - Add `--lang en` only if the user says the document is English-only.
   - Add `--force` only if the user asks to re-convert.
   - Add `--allow-online-audio` only if the user explicitly accepts sending audio to Google.
3. Parse the JSON on stdout. Exit code 1 → report `error_kind`/`error_msg` verbatim and stop. If the message says `run aidoc setup <engine>`, tell the user that command.
4. Read the Markdown file inside `output_dir`: it is named after the directory itself, i.e. `<output_dir>/<last path component of output_dir>.md` (images are under `<output_dir>/assets/`). Use it to answer the user's request.
5. If `quality.level == "low"` (or `status == "low"`), say so up front and list `quality.reasons` (e.g. `chars_per_page`, `garbage_ratio`, `missing_table`) so the user knows the text may be incomplete. Mention `tried` (which engines were attempted).
6. Never paste the whole Markdown back unless asked; summarise or quote the relevant parts. Page numbers are available from `<!-- page: N -->` markers — cite them.

## Notes
- Large PDFs are converted in 40-page segments; an interrupted run resumes when the same command is re-run.
- Already-converted files (same sha256) return `status: "skipped"` immediately; the `.md` is still valid.
- `error_msg` starting with `already_converting` means another aidoc process is converting that file right now: wait and re-run.
