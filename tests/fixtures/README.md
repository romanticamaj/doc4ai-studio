# Test fixtures

Files are generated; do not edit by hand. Regenerate with:

    uv run python tests/fixtures/make_fixtures.py

(needs a CJK font; on Windows `C:\Windows\Fonts\msjh.ttc` is used, else PyMuPDF's built-in `cjk` font).
The same script copies self-check samples into `src/aidoc/engines/runner/samples/`.

| File | Content | Used by |
|---|---|---|
| `text.pdf` | 3 pages, English + 繁中 paragraphs (≥ 300 chars/page), page 2 has a ruled 3×4 table | probe (text_ratio=1, has_table_lines), Docling integration |
| `scanned_cht.pdf` | 2 pages, each a full-page PNG rendered from 繁中 text (no text layer) | probe scanned, MinerU integration |
| `scanned_mixed.pdf` | 2 pages image-only, 中英混排 | MinerU integration |
| `scanned_en.pdf` | 1 page image-only English | §13.2 routing (lang=en → docling) |
| `formula.pdf` | 2 pages, base-14 `Symbol` font + `∑∫∂√` density | probe math_hint |
| `twocol.pdf` | 2 pages, two text columns | probe layout_hint |
| `big.pdf` | 45 pages text | segmentation (P2), integration |
| `blank.pdf` | 1 empty page | quality/probe edge |
| `encrypted.pdf` | AES-256 user password | probe error=encrypted |
| `corrupt.pdf` | 2 KB random bytes with `%PDF-` header | probe error=corrupt |
| `page.png` | page image with 繁中 + English, 150 dpi | image routing, MinerU |
| `sample.docx` | headings, paragraphs (中英), 1 table, 1 embedded PNG | MarkItDown + image extraction |
| `sample.pptx` | 3 slides, text + 1 picture on slide 2 | MarkItDown slide markers + image extraction |
| `sample.xlsx` | 2 sheets, small tables | MarkItDown |
| `sample.html` | headings + table | MarkItDown |
| `span_margin.pdf` | 45 pages, 1-inch margins, page-number footer; a ruled table runs from the bottom of page 40 onto the top of page 41 | cross-segment table join (P2 verifier I2) |
| `bad.exe` | 10 bytes | unsupported type |
| `paged_furniture.pdf` | 8 pages A4, Helvetica: running header `Chapter 2 - Testing Pages` + footer page number on every non-blank page; page 2 table of contents with dot leaders; page 4 blank; a paragraph runs from the bottom of page 5 onto page 6; page 7 drawn figure + caption `Figure 7.1 A drawn box`; ≥ 600 chars of unique English per body page | probe (blank page, no broken fonts), MinerU per-page render plan (recorded `mineru_recorded/furniture8/`), page-map slow tests (spec 2026-10-01) |
| `broken_tounicode.pdf` | 2 pages; Helvetica heading + 6 lines; page 1 also has `Wouldn't it be dreamy if there was a book on Android …` in PyMuPDF's built-in `cjk` font (Type0/Identity-H) with `/ToUnicode` deleted, page 2 uses that font only for a footer `7` — the text layer is glyph ids (T-001 reproduction) | probe broken fonts, T-001 detection/repair tests (Docling pypdfium backend, auto-routing fallback) |
| `mineru_recorded/furniture8/` | real MinerU 4.0.7 `markdown.md` + `middle_json.json` of `paged_furniture.pdf` (8 pages, page 4 blank); recorded with `tests/fixtures/_tmp` + `mineru.parser.parse(..., tier="basic", ocr_mode="auto").save(...)`, not generated | MinerU per-page render plan test (`tests/integration/test_mineru_pages_env.py`) |

Real samples live in `tests/fixtures/manual/` (gitignored, never committed — copyrighted books). They are listed in the
committed `manual_samples.json` and obtained through `tests/manual.py` (`manual_sample(name)`, `require_engine(name)`):
missing samples skip normally and **fail** with `AIDOC_REQUIRE_MANUAL=1` (spec 2026-10-01 §10).
