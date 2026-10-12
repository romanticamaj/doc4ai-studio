from pathlib import Path

import pymupdf

from aidoc.pagemap import (
    MARKER,
    apply_inferred,
    infer_pages,
    pdf_text_layers,
    split_pages,
    squash,
    squash_with_map,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _marked_markdown_from_pdf(pdf):
    # our own output stand-in: the PDF text layer itself, one section per page, with markers
    with pymupdf.open(pdf) as d:
        return "".join(f"<!-- page: {i} -->\n\n{p.get_text().strip()}\n\n" for i, p in enumerate(d, 1))


def test_squash_with_map_matches_squash_and_maps_back():
    s = "The Quick-\nbrown ﬁsh 臺灣­大學!"
    sq, pos = squash_with_map(s)
    assert sq == squash(s) and len(pos) == len(sq)
    assert s[pos[sq.index("brown")]] == "b" and s[pos[sq.index("臺")]] == "臺"


def test_infers_boundaries_of_a_marker_stripped_document():
    pdf = FIX / "paged_furniture.pdf"
    marked = _marked_markdown_from_pdf(pdf)
    stripped = MARKER.sub("", marked)
    res = infer_pages(stripped, pdf_text_layers(pdf))
    assert res["method"] == "inferred" and res["coverage"] is None and res["found"] is None and res["expected"] == 8
    assert res["anchored"] >= 6 and res["confidence"] >= 0.85          # page 4 is blank: undecidable by design
    assert 4 in res["undecidable"] and res["dropped_candidates"] == []
    rebuilt = apply_inferred(stripped, res["sections"])
    _, secs = split_pages(rebuilt)
    _, orig = split_pages(marked)
    for p in (1, 2, 3, 5, 6, 7, 8):
        assert secs[p].strip()[:40] == orig[p].strip()[:40], p        # boundaries land on the right paragraph
    assert MARKER.sub("", rebuilt).replace("\n", "") == stripped.replace("\n", "")   # nothing lost or duplicated


def test_monotone_filter_drops_out_of_order_anchor():
    pdf = FIX / "paged_furniture.pdf"
    layers = pdf_text_layers(pdf)
    marked = _marked_markdown_from_pdf(pdf)
    _, secs = split_pages(marked)
    swapped = "\n\n".join(secs[p] for p in (1, 2, 6, 4, 5, 3, 7, 8))   # pages 3 and 6 swapped, no markers
    res = infer_pages(swapped, layers)
    assert res["dropped_candidates"] and set(res["dropped_candidates"]) <= {3, 6}
    assert res["out_of_order"] and set(res["out_of_order"]) <= {3, 6}


def test_dropped_page_between_anchored_neighbours_gets_an_empty_section():
    pdf = FIX / "paged_furniture.pdf"
    _, secs = split_pages(_marked_markdown_from_pdf(pdf))
    md = "\n\n".join(secs[p] for p in (1, 2, 3, 4, 6, 7, 8))           # page 5 silently lost
    res = infer_pages(md, pdf_text_layers(pdf))
    assert res["dropped_candidates"] == [5] and res["sections"][5][0] == res["sections"][5][1]
    _, got = split_pages(apply_inferred(md, res["sections"]))
    assert got[5].strip() == "" and got[6].strip()[:40] == secs[6].strip()[:40]


def test_scanned_pdf_has_no_layers_so_nothing_is_anchored():
    res = infer_pages("some text that is not in any layer", pdf_text_layers(FIX / "scanned_cht.pdf"))
    assert res["anchored"] == 0 and res["confidence"] == 0.0 and res["sections"] == {}
