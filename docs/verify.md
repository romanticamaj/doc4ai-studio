# Verify any Markdown against its PDF

`doc4ai verify` checks any Markdown against its source PDF, page by page — our own output or one made by MinerU, Docling,
MarkItDown or another tool — with the same page model the converter uses. It reads `<!-- page: N -->` markers, the
MinerU / Docling / pdfmux marker styles or a custom marker template (`[[p {page}]]`: literal text around one page number — no free-form regex), and infers the page boundaries from the PDF text layer when
there are no markers.

```bash
uv run doc4ai verify out/<stem>/                               # one of our outputs (sidecar + source found for you)
uv run doc4ai verify book.md --pdf book.pdf --json report.json --html report.html --thumbnails   # any tool's Markdown
uv run doc4ai verify book.md --pdf book.pdf --fail-on warn     # CI: exit 1 on warn or low
```

It reports dropped pages (`page_dropped`: the PDF page has text, the Markdown section is empty), page markers that are
missing, shifted or out of order, broken text layers, garbage, and — as advisories that never change the level — tables
or formulas that seem missing on a page. Exit codes: `0` passed, `1` failed `--fail-on` (default `low`), `3`
unverifiable, `4` usage or input error. The same check is `POST /api/verify` and the MCP tools `verify_document` /
`verify_markdown`; none of them writes anything.

What it cannot do: it sees structural losses only, not OCR misreadings, VLM hallucinations or text missed inside
images. A scanned PDF whose Markdown has no page markers cannot be verified (no text layer to anchor on: exit 3).
`page_dropped` also fires when a page is one big picture and the engine rightly kept only the image (use
`--thumbnails` to look). Table / formula advisories come from ruling-line and math-font heuristics. Inferred page
boundaries need text the PDF layer and the Markdown share; pages that are mostly tables may stay undecidable, and on
multi-column books the text layer's order can differ from the reading order, so a boundary may sit a paragraph or
two off.

Known limits of the per-page checks:

- `page_partial` (a page whose section holds clearly less than half of its text) is an **advisory**: shown on the
  page, it never raises the level, triggers a repair or an engine fallback, until it has passed on a blind held-out
  page set. Code listings, tables and body text count as page text; callouts and labels inside a figure kept as a
  picture do not — but only lines over an embedded picture are recognised as figure lines: labels of a diagram
  drawn as vectors, and lines shorter than 20 characters (table cells), are left out of the comparison too.
- Code that exists only as an image in the PDF has no text layer to compare with: its loss is not seen.
- A paragraph continued across a page break, or across the columns of a two-column page, often lands under the
  previous page's marker; that is not flagged (it would be on every page of some books), so a page whose whole
  first column moved is under-reported.
- A document whose source PDF is gone cannot be re-graded: it keeps its last result (for example a stale `warn`)
  until it is uploaded or converted again.
- Text layers drawn through a wrong Unicode map (shifted-cipher fonts) are detected by a language-model check and
  left out of the coverage checks; such pages are judged only on an empty section.
- Garbage that is identical in the text layer and in the output is not caught (it "matches"); a script-consistency
  check is deferred.
- Moved text can be missed on noisy OCR text layers (the "distinctive n-grams" are unstable under OCR errors).
- Whether a paragraph continued across a page break is a normal continuation or a moved block is a judgement call.
- Blind precision / recall is not measured yet: the independent held-out set (183 pages) has only 1–4 positives per
  reason, so the label gate reports an expected failure (xfail), not a pass.
- The calibration gate fails (ρ 0.63, precision 0.83, recall 0.72), so the benchmark is not published; those gate
  numbers predate commit 4a2dd90 (broken-heading garbage check), which the gate was not re-run for.
