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

Real scanned documents may be placed in `tests/fixtures/manual/` (gitignored) and are picked up by
`tests/integration/test_manual_fixtures.py` when present.
