# Output and source copies

Everything goes into one **Doc4AI folder** — Settings → 「Doc4AI 資料夾」, `[general] root_dir` in `doc4ai.toml`, or
the `DOC4AI_LIBRARY_ROOT` environment variable. When none is set it is `C:\doc4ai` on Windows and `~/doc4ai` on
macOS/Linux (created on first use; a folder that cannot be created fails the job with its path, it never falls back
elsewhere). The old `general.output_dir` key is no longer used for new conversions.

```
<Doc4AI folder>/
├─ out/<name>/
│  ├─ <name>.md      Markdown with <!-- page: N --> markers
│  ├─ <name>.json    engine, attempts, probe results, quality score and per-page flags, timings, source_copy
│  └─ assets/        extracted images
└─ source/<name>/<original file name>   a permanent copy of the input file
```

Every conversion keeps a **source copy**: copied from the sha256-verified work copy, hashed again after writing,
written atomically, never removed by the 7-day work-copy cleanup, and hardlinked when two documents have the same
content. When the original is moved or deleted, the Document view, Office preview, `doc4ai verify`, re-assessment and
「重新轉換」 use the copy. A copy that fails (disk full, a locked file) never fails the conversion: the document is
marked 「未備份」 in the Library and can be retried from the Document view. Changing the Doc4AI folder never moves
converted documents; only new conversions go to the new folder.

Documents converted before source copies existed get one with a **backfill**, which copies only what sha256 still
proves (the retained work copy, the original, or another document with the same content) and lists the rest:

```bash
uv run doc4ai sources backfill --dry-run   # report only, writes nothing
uv run doc4ai sources backfill             # exit 0 all copied, 3 some cannot be recovered, 1 unreadable/failed
uv run doc4ai sources status
```

The same is in Settings → 「原始檔備份」 (「檢查（不寫入）」 / 「補齊備份」).
