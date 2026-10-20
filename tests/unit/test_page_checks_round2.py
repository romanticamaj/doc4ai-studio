"""Verifier round 2 on the per-page checks (spec 2026-10-05 verify §2.3): full-page tables with short cells, merged
tables, single-probe hosts, repeated sentences, furniture-only pages, link URLs, blank hosts, re-validated repairs,
scans with markers."""
from pathlib import Path

import pymupdf
import pytest

from aidoc.pagemap import pdf_layers
from aidoc.probe import probe_file
from aidoc.quality import assess, page_checks

WORDS = ["orthopaedic", "medicine", "relies", "on", "careful", "clinical", "examination", "of", "moving", "parts", "where", "each", "structure", "is", "tested", "by", "passive", "active", "and", "resisted", "movements", "and", "the", "pattern", "of", "pain", "and", "limitation", "points", "to", "the", "lesion", "while", "rehabilitation", "proceeds", "in", "stages", "from", "protection", "to", "range", "of", "motion", "then", "strengthening", "against", "resistance", "until", "return", "to", "sport", "after", "months", "of", "graded", "work", "guided", "by", "a", "physiotherapist", "who", "records", "every", "finding"]


def _sentence(seed: int, n: int = 14) -> str:
    return " ".join(WORDS[(seed * 7 + i * (seed % 5 + 1)) % len(WORDS)] for i in range(n)).capitalize() + f" {seed}."


def _pdf(tmp_path: Path, pages: list[list[str]], *, header: bool = True, name: str = "t.pdf") -> Path:
    doc = pymupdf.open()
    for n, lines in enumerate(pages, 1):
        p = doc.new_page(width=595, height=842)
        if header:
            p.insert_text((50, 30), "A SYSTEM OF ORTHOPAEDIC MEDICINE - CHAPTER SEVEN - THE SHOULDER REGION", fontsize=8)
            p.insert_text((50, 825), f"Copyright 2013 Elsevier Ltd. All rights reserved. Page {n}", fontsize=8)
        y = 60
        for ln in lines:
            p.insert_text((50, y), ln, fontsize=9)
            y += 13
    out = tmp_path / name
    doc.save(out)
    return out


def _text_page(seed: int, k: int = 12) -> list[str]:
    return [_sentence(seed * 100 + i) for i in range(k)]


def _run(pdf: Path, secs: dict[int, str]) -> dict[int, dict]:
    md = "".join(f"<!-- page: {p} -->\n\n{secs[p]}\n\n" for p in sorted(secs))
    layers, content, body, untrusted = pdf_layers(pdf)
    pr = probe_file(pdf)
    return {e["page"]: e for e in page_checks(md, layers, content, pr.blank_pages, untrusted, body=body)}


def _own(pages: list[list[str]]) -> dict[int, str]:
    return {i: "\n".join(ln) for i, ln in enumerate(pages, 1)}


def _table_page(seed: int) -> list[str]:
    """A full-page table: ~90 short cells (codes, numbers, two-word labels) — no prose line at all."""
    return [f"{WORDS[(seed + i) % len(WORDS)][:8]} {i * 3 + seed} mg" for i in range(90)]


def test_full_page_table_lost_to_a_picture_is_dropped(tmp_path):
    """Finding A (Ortho p281): a full-page table has ~1000 text-layer chars but almost no prose line. The section
    holds only a picture: page_dropped — dropped is judged on the whole (furniture-free) text layer."""
    pages = [_text_page(1), _table_page(2), _text_page(3), _text_page(4)]
    secs = _own(pages)
    secs[2] = "![](assets/p2_1.jpg)"
    got = _run(_pdf(tmp_path, pages), secs)
    assert got[2]["reasons"] == ["page_dropped"] and set(got) == {2}


def test_heading_only_section_is_dropped_not_partial(tmp_path):
    pages = [_text_page(1), _text_page(2), _text_page(3), _text_page(4)]
    secs = _own(pages)
    secs[3] = "## Interpretation of the clinical examination\n\n![](assets/p3_1.jpg)\n\n© Copyright 2013 Elsevier"
    got = _run(_pdf(tmp_path, pages), secs)
    assert got[3]["reasons"] == ["page_dropped"] and set(got) == {3}


def test_table_merged_into_previous_page_is_misaligned_not_dropped(tmp_path):
    """Finding B (Ortho 1051-1054): MinerU merged the next pages' table rows into one HTML table on the first page.
    The probes never matched across <td> tags, the pages looked dropped and were OCR-copied a second time. Their text
    is in page 2's section once tags are stripped: page_misaligned (never repaired), page 2 page_oversized."""
    pages = [_text_page(1), _table_page(2), _table_page(3), _table_page(4), _text_page(5)]
    secs = _own(pages)
    rows = [ln for p in (2, 3, 4) for ln in pages[p - 1]]
    secs[2] = "<table>" + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r.split(" ", 1)) + "</tr>"
                                  for r in rows) + "</table>"
    secs[3] = secs[4] = ""
    got = _run(_pdf(tmp_path, pages), secs)
    assert got[3]["reasons"] == ["page_misaligned"] and got[4]["reasons"] == ["page_misaligned"]
    assert 2 in got[3]["metrics"]["found_in"]
    assert got[2]["reasons"] == ["page_oversized"] and {3, 4} <= set(got[2]["metrics"]["foreign_pages"])
    assert not any("page_dropped" in e["reasons"] for e in got.values())


def test_one_shared_sentence_does_not_make_a_host(tmp_path):
    """Finding B (Ortho 476 / 677): one probe of a page found in a neighbour (a sentence carried over a page break)
    is not "that section holds another page's text"."""
    pages = [_text_page(1), _text_page(2), _text_page(3)]
    secs = _own(pages)
    secs[1] = secs[1] + "\n" + pages[1][5]          # page 2's sixth sentence also in page 1
    got = _run(_pdf(tmp_path, pages), secs)
    assert got == {}


def test_page_of_repeated_sentences_moved_to_a_neighbour_is_misaligned(tmp_path):
    """No unique probe exists on a page that repeats one sentence (edge3.pdf p1): the n-gram check still sees that
    its text sits under the next marker."""
    rep = [_sentence(7)] * 12
    pages = [_text_page(1), rep, _text_page(3)]
    secs = _own(pages)
    secs[3] = "\n".join(rep) + "\n" + secs[3]
    secs[2] = ""
    got = _run(_pdf(tmp_path, pages), secs)
    assert got[2]["reasons"] == ["page_misaligned"] and got[3]["reasons"] == ["page_oversized"]


def test_furniture_only_and_caption_only_pages_are_not_dropped(tmp_path):
    """Verifier minor (edge3.pdf): the furniture filter switched itself off on a page whose text is only the running
    header and footer (more than half of its text), so a header/footer-only page and a figure page with a tiny
    caption looked like full pages and were pushed to another engine."""
    pages = [_text_page(1), [], ["Fig 7.1 Shoulder."], _text_page(4), _text_page(5), [], _text_page(7)]
    secs = _own(pages)
    secs[2] = ""
    secs[3] = "Fig 7.1 Shoulder."
    pdf = _pdf(tmp_path, pages)
    _, _, body, _ = pdf_layers(pdf)
    assert body[1] == "" and body[2] == "fig71shoulder"
    assert _run(pdf, secs) == {}


def test_link_urls_do_not_count_toward_the_section_size(tmp_path):
    """Verifier minor (GEO report p14): a page of links is not oversized because of its URLs."""
    pages = [_text_page(1), [f"See source {i} for details" for i in range(30)], _text_page(3)]
    secs = _own(pages)
    secs[2] = "\n".join(f"[See source {i}](https://example.org/{'x' * 120}/{i}) for details" for i in range(30))
    got = _run(_pdf(tmp_path, pages), secs)
    assert 2 not in got


def test_near_empty_page_holding_another_pages_text_is_oversized(tmp_path):
    """Finding B: the "< 20 layer chars" exemption must not hide a near-blank page whose section holds another
    page's text."""
    pages = [_text_page(1), ["2"], _text_page(3)]
    secs = _own(pages)
    secs[2] = secs[3]
    secs[3] = ""
    got = _run(_pdf(tmp_path, pages), secs)
    assert got[3]["reasons"] == ["page_misaligned"] and got[2]["reasons"] == ["page_oversized"]


def test_solution_page_repeating_the_exercise_is_not_oversized(tmp_path):
    """A page whose own text layer repeats a neighbour's text (exercise / solution pages) holds no foreign text."""
    ex = _text_page(2)
    pages = [_text_page(1), ex, ex + [_sentence(999)], _text_page(4)]
    got = _run(_pdf(tmp_path, pages), _own(pages))
    assert got == {}


def test_regrade_rechecks_a_page_with_an_old_repair(tmp_path):
    """Verifier minor: a re-grade kept `repaired_by` from history without looking at the page again. A repair whose
    section still fails the page checks is no longer counted as repaired."""
    pages = [_text_page(1), _text_page(2), _text_page(3)]
    secs = _own(pages)
    secs[2] = "## Heading only"
    pdf = _pdf(tmp_path, pages)
    md = "".join(f"<!-- page: {p} -->\n\n{secs[p]}\n\n" for p in sorted(secs))
    pr = probe_file(pdf)
    old = [{"page": 2, "reasons": ["page_dropped"], "repaired_by": "mineru:ocr"}]
    q = assess(md, pr, pr.pages, pdf=pdf, page_map_method="x", repaired=old, last_resort=True)
    e = next(e for e in q.pages if e["page"] == 2)
    assert "repaired_by" not in e and e["reasons"] == ["page_dropped"] and e["repair_rejected"] == "mineru:ocr"
    assert q.pages_unrepaired >= 1
    secs[2] = "\n".join(pages[1])
    md = "".join(f"<!-- page: {p} -->\n\n{secs[p]}\n\n" for p in sorted(secs))
    q = assess(md, pr, pr.pages, pdf=pdf, page_map_method="x", repaired=old, last_resort=True)
    assert next(e for e in q.pages if e["page"] == 2)["repaired_by"] == "mineru:ocr"


@pytest.mark.parametrize("fixture", ["scanned_en.pdf"])
def test_scan_with_markers_is_partially_verifiable(fixture):
    """Verifier minor: a scanned PDF (no text layer) with page markers only gets marker coverage: "partial"."""
    from aidoc.verify import verify_markdown
    pdf = Path(__file__).resolve().parents[1] / "fixtures" / fixture
    n = probe_file(pdf).pages
    md = "".join(f"<!-- page: {i} -->\n\ntext {i}\n\n" for i in range(1, n + 1))
    assert verify_markdown(md, pdf, fmt="aidoc").verifiable == "partial"     # one page: detect needs two markers


def test_xml_code_listing_is_page_text_not_html():
    """Verifier round 2 (found on the labelled Head First pages): a generic tag pattern removed `<LinearLayout ...>`
    from code listings, so complete code pages read as page_partial. Only HTML that engines emit is stripped;
    character entities are decoded."""
    from aidoc.quality import section_text
    assert section_text('<Button android:text="Save" />') == "buttonandroidtextsave"
    assert section_text("&lt;Button android:text=&quot;Save&quot; /&gt;") == "buttonandroidtextsave"
    assert section_text("<table><tr><td>a1</td><td>b2</td></tr></table><br/>x<sub>2</sub>") == "a1b2x2"


def test_broken_font_spans_are_left_out_of_the_layer():
    """Head First marks 339 of 532 pages "broken font" (handwriting annotation fonts) while their body text and code
    extract fine: those pages are checked on their healthy-font text instead of skipping the checks (verifier
    round 2). The fixture's broken Type0 lines (shift-cipher garbage) are gone, its Helvetica lines stay."""
    from aidoc.pagemap import page_lines
    with pymupdf.open(Path(__file__).resolve().parents[1] / "fixtures" / "broken_tounicode.pdf") as d:
        lines, untrusted, _ = page_lines(d)
        raw = d[0].get_text()
    assert "8PVMEO" in raw and not any("8PVMEO" in ln for ln in lines[0])
    assert any("Normal Helvetica line 1" in ln for ln in lines[0]) and untrusted == []


def _shift(s: str, k: int = 29) -> str:
    return "".join(chr(ord(c) - k) if c.isalpha() and ord(c) - k > 32 else c for c in s)


def test_shifted_cipher_text_layer_is_untrusted_not_partial(tmp_path):
    """Verifier round 3: a text layer drawn through a wrong Unicode map (a shifted cipher of the real text) gave
    coverage 0.0 and 34 false page_partial flags on the synthetic broken-font samples while the Markdown was right.
    A page whose layer reads far less like the document's language than its own section is untrusted: no coverage
    checks there."""
    from aidoc.quality import language_gap, untrusted_layers
    pages = [_text_page(i) for i in range(1, 7)]
    pdf = _pdf(tmp_path, pages)
    layers, content, body, untrusted = pdf_layers(pdf)
    secs = _own(pages)
    md = "".join(f"<!-- page: {p} -->\n\n{secs[p]}\n\n" for p in sorted(secs))
    from aidoc.pagemap import squash
    cipher = squash(_shift(" ".join(pages[2])))
    layers2 = list(layers)
    layers2[2] = cipher
    content2, body2 = list(content), list(body)
    content2[2] = body2[2] = cipher
    assert language_gap(md, layers2)[3] > 1.5 and language_gap(md, layers)[3] < 0.5
    assert untrusted_layers(md, layers2) == {3}
    got = {e["page"]: e for e in page_checks(md, layers2, content2, (), untrusted, body=body2)}
    assert got == {}


def test_callouts_on_a_picture_are_not_page_prose(tmp_path):
    """Verifier round 3 policy: code listings, tables and body text count as page text for page_partial; callouts
    and labels inside a figure kept as a picture do not. Lines whose centre lies in a picture are left out of the
    prose layer (they stay in the body layer, which page_dropped uses)."""
    doc = pymupdf.open()
    body = [_sentence(500 + i) for i in range(6)]
    callouts = [f"This callout explains detail number {i} of the screenshot above it" for i in range(8)]
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    pix.clear_with(200)
    for n in range(3):
        p = doc.new_page(width=595, height=842)
        y = 60
        for ln in body:
            p.insert_text((50, y), ln + f" {n}", fontsize=9)
            y += 13
        if n == 1:
            p.insert_image(pymupdf.Rect(50, 200, 545, 700), pixmap=pix)
            for k, c in enumerate(callouts):
                p.insert_text((70, 240 + 50 * k), c, fontsize=10)
    pdf = tmp_path / "callouts.pdf"
    doc.save(pdf)
    layers, prose, body_l, untrusted = pdf_layers(pdf)
    assert "callout" not in prose[1] and "callout" in body_l[1]
    md = "".join(f"<!-- page: {n + 1} -->\n\n" + "\n".join(ln + f" {n}" for ln in body) + "\n\n![](a.png)\n\n"
                 for n in range(3))
    got = {e["page"]: e for e in page_checks(md, layers, prose, (), untrusted, body=body_l)}
    assert 2 not in got
