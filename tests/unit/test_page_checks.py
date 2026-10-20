"""Page-level structural checks shared by conversions and verify (verifier round on spec 2026-10-05 verify §2.3):
page_dropped, page_partial, page_misaligned, page_oversized; furniture-stripped text layers; repair acceptance by
text-layer coverage."""
from pathlib import Path

import pymupdf

from aidoc.pagemap import content_layers, pdf_layers, split_pages
from aidoc.probe import probe_file
from aidoc.quality import assess, coverage, page_checks

FIX = Path(__file__).resolve().parents[1] / "fixtures"
PF = FIX / "paged_furniture.pdf"


def _own(pdf):
    with pymupdf.open(pdf) as d:
        return "".join(f"<!-- page: {i} -->\n\n{p.get_text().strip()}\n\n" for i, p in enumerate(d, 1))


def _md(secs: dict[int, str]) -> str:
    return "".join(f"<!-- page: {p} -->\n\n{secs[p]}\n\n" for p in sorted(secs))


def _checks(md, pdf=PF):
    layers, content, body, untrusted = pdf_layers(pdf)
    pr = probe_file(pdf)
    return {e["page"]: e for e in page_checks(md, layers, content, pr.blank_pages, untrusted, body=body)}


def test_clean_output_has_no_page_flags():
    for pdf in (PF, FIX / "text.pdf", FIX / "big.pdf", FIX / "twocol.pdf"):
        assert _checks(_own(pdf), pdf) == {}, pdf.name


def test_running_header_and_page_number_are_not_page_content():
    layers, content, _, _ = pdf_layers(PF)
    assert all("chapter2testingpages" not in c for c in content)          # the running header is gone
    assert all(len(c) < len(raw) for c, raw in zip(content, layers, strict=True) if raw)
    pages = [["Chapter 2", str(n), f"Body text of page {w} goes here", f"and {w} continues on this line"]
             for n, w in ((1, "one"), (2, "two"), (3, "three"))]
    assert content_layers(pages)[1] == "bodytextofpagetwogoeshereandtwocontinuesonthisline"
    # a page that is nothing but repeated lines keeps them (more than half of its text would go)
    assert content_layers([["Same line", "1"]] * 3) == ["sameline1"] * 3


def test_figure_page_with_only_header_labels_and_caption_is_not_dropped():
    """Verifier minor: a figure page whose text layer is the running header, the page number, diagram labels and a
    caption is not a page full of text: an engine that keeps the figure as a picture has not dropped the page."""
    body = [f"Body line {n} of a real page with plenty of words to read here" for n in range(6)]
    pages = [["A SYSTEM OF MEDICINE - CHAPTER SEVEN", str(n)] + [f"{b} p{n}" for b in body] for n in range(1, 5)]
    pages.append(["A SYSTEM OF MEDICINE - CHAPTER SEVEN", "5", "Arthrosis", "Spondylosis", "Bone disease",
                  "Fig 7.6 Interpretation of neck movements.", "Recent fracture", "Rheumatoid arthritis",
                  "Costoscapuloclavicular lesion", "Ankylosing spondylitis"])
    content = content_layers(pages, prose_only=True)
    from aidoc.pagemap import squash
    raw = [squash(" ".join(pg)) for pg in pages]
    assert len(raw[4]) >= 120 and len(content[4]) < 120
    md = "".join(f"<!-- page: {i} -->\n\n{' '.join(pages[i - 1][2:])}\n\n" for i in range(1, 5))
    md += "<!-- page: 5 -->\n\n![](assets/p5_1.png)\n\n"
    assert page_checks(md, raw, content) == []


def test_misplaced_page_is_misaligned_not_dropped():
    """Docling put page 7's text into page 5's section (verifier fixture, a blank page 6 between): page 7 must be
    page_misaligned (its text is elsewhere), never dropped and OCR-repaired into a duplicate."""
    _, secs = split_pages(_own(PF))
    secs = dict(secs)
    secs[5] = secs[5] + "\n\n" + secs[7]
    secs[7] = ""
    got = _checks(_md(secs))
    assert got[7]["reasons"] == ["page_misaligned"] and got[7]["metrics"]["found_in"] == [5]
    assert got[5]["reasons"] == ["page_oversized"] and 7 in got[5]["metrics"]["foreign_pages"]
    assert all("page_dropped" not in e["reasons"] for e in got.values())


def test_swapped_content_under_valid_markers_is_misaligned():
    _, secs = split_pages(_own(PF))
    secs = dict(secs)
    secs[3], secs[6] = secs[6], secs[3]
    got = _checks(_md(secs))
    assert {p for p, e in got.items() if "page_misaligned" in e["reasons"]} == {3, 6}


def test_partial_page_is_flagged_with_its_coverage():
    _, secs = split_pages(_own(PF))
    secs = dict(secs)
    secs[5] = secs[5][: int(len(secs[5]) * 0.3)]
    got = _checks(_md(secs))
    assert got[5]["advisories"] == ["page_partial"] and got[5]["metrics"]["coverage"] < 0.5   # advisory (round 3)
    assert set(got) == {5}


def test_dropped_page_still_dropped():
    _, secs = split_pages(_own(PF))
    secs = dict(secs)
    secs[3] = ""
    got = _checks(_md(secs))
    assert got[3]["reasons"] == ["page_dropped"] and set(got) == {3}


def test_coverage_measures_how_much_of_the_layer_a_section_holds():
    layer = "thequickbrownfoxjumpsoverthelazydog" * 10
    assert coverage("The quick brown fox jumps over the lazy dog. " * 10, layer) > 0.95
    assert coverage("## The quick\n\n![](a.png)", layer) < 0.1
    assert coverage("", layer) == 0.0 and coverage("x", "") is None


def test_assess_counts_the_new_reasons_as_flagged_pages():
    _, secs = split_pages(_own(PF))
    secs = dict(secs)
    secs[3], secs[6] = secs[6], secs[3]
    pr = probe_file(PF)
    q = assess(_md(secs), pr, pr.pages, pdf=PF, page_map_method="x")
    assert {e["page"] for e in q.pages if "page_misaligned" in e["reasons"]} == {3, 6}
    assert q.level in ("warn", "low") and q.pages_unrepaired >= 2
