import pytest

from aidoc.markers import DIALECTS, detect, to_aidoc


def test_detect_dialects():
    assert detect("<!-- page: 1 -->\na\n<!-- page: 2 -->\nb") == "aidoc"
    assert detect("<!-- page 1 -->\na\n<!-- page 2 -->\nb") == "pdfmux"
    assert detect("a\n<!-- page break -->\nb\n<!-- page break -->\nc") == "docling"
    assert detect("a\n\n---\n\nb\n\n---\n\nc") == "mineru"
    assert detect("plain text without any markers\n\n---\n\nonly one rule") == "none"
    assert detect("| a | b |\n| --- | --- |\n| 1 | 2 |\n\n| c |\n| --- |\n") == "none"   # table rules are not pages


def test_dialect_table_lists_every_format():
    assert set(DIALECTS) == {"aidoc", "pdfmux", "docling", "mineru", "markitdown"}
    assert DIALECTS["aidoc"].numbered and not DIALECTS["docling"].numbered


def test_numbered_dialect_is_renamed():
    md, info = to_aidoc("<!-- page 1 -->\na\n<!-- page 2 -->\nb", "pdfmux", expected_pages=2)
    assert md.count("<!-- page: ") == 2 and info["applied"] and info["markers_found"] == 2 and info["numbered"]
    assert "<!-- page: 2 -->\nb" in md


def test_aidoc_markers_are_kept_verbatim():
    src = "<!-- page: 1 -->\na\n<!-- page: 2 -->\nb"
    md, info = to_aidoc(src, "aidoc", expected_pages=2)
    assert md == src and info["applied"] and info["markers_found"] == 2


def test_unnumbered_dialect_needs_matching_page_count():
    src = "a\n<!-- page break -->\nb\n<!-- page break -->\nc"
    md, info = to_aidoc(src, "docling", expected_pages=3)
    assert md.startswith("<!-- page: 1 -->") and "<!-- page: 3 -->" in md and info["applied"]
    assert "page break" not in md
    md2, info2 = to_aidoc(src, "docling", expected_pages=5)          # Docling drops blank pages: 3 sections != 5 pages
    assert md2 == src and not info2["applied"] and info2["reason"] == "section_count_mismatch"
    md3, info3 = to_aidoc(src, "docling", expected_pages=None)
    assert md3 == src and not info3["applied"] and info3["reason"] == "page_count_unknown"


def test_mineru_rules_number_sections():
    md, info = to_aidoc("a\n\n---\n\nb\n\n---\n\nc", "mineru", expected_pages=3)
    assert info["applied"] and md.count("<!-- page: ") == 3 and "---" not in md


def test_none_and_markitdown_are_never_applied():
    _, info = to_aidoc("<!-- page: 1 -->\nx", "none", expected_pages=1)
    assert not info["applied"] and info["reason"] == "forced_none"
    _, info = to_aidoc("<!-- Slide number: 1 -->\nx\n<!-- Slide number: 2 -->\ny", "markitdown", expected_pages=2)
    assert not info["applied"] and info["markers_found"] == 2 and info["reason"] == "markitdown_pdf_has_no_markers"


def test_numbered_dialect_without_markers():
    md, info = to_aidoc("no markers", "pdfmux", expected_pages=2)
    assert md == "no markers" and not info["applied"] and info["reason"] == "no_markers"


def test_custom_regex_requires_one_group():
    with pytest.raises(ValueError):
        to_aidoc("x", "custom", expected_pages=1, marker_regex=r"\[\[page\]\]")
    with pytest.raises(ValueError):
        to_aidoc("x", "custom", expected_pages=1, marker_regex=r"([")
    md, info = to_aidoc("[[p 1]]\na\n[[p 2]]\nb", "custom", expected_pages=2, marker_regex=r"\[\[p (\d+)\]\]")
    assert md.count("<!-- page: ") == 2 and info["format"] == "custom"


def test_unknown_format_is_rejected():
    with pytest.raises(ValueError):
        to_aidoc("x", "pandoc", expected_pages=1)


@pytest.mark.parametrize("bad", [r"\[\[p (\d+)?\]\]", r"\[\[p (\d*)\]\]", r"(?:\[\[p (\d+)\]\]|<p>)", r"((a+)+\d)",
                                 r"[[p (\d+)" + "x" * 300])
def test_custom_regex_is_bounded_and_its_group_always_captures(bad):
    """Verifier minor: a regex longer than MARKER_REGEX_MAX, with an optional / possibly empty capture group or a
    nested unbounded quantifier (catastrophic backtracking) is refused with ValueError (API 422, CLI exit 4)."""
    with pytest.raises(ValueError):
        to_aidoc("[[p 1]]\na", "custom", expected_pages=1, marker_regex=bad)


def test_safe_custom_regex_still_works():
    md, info = to_aidoc("[[p 1]]\na\n[[p 2]]\nb", "custom", expected_pages=2, marker_regex=r"\[\[p (\d{1,5})\]\]")
    assert info["applied"] and md.count("<!-- page: ") == 2
