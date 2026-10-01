from aidoc.probe import kind_for, probe_file


def test_text_pdf(fixtures):
    p = probe_file(fixtures / "text.pdf")
    assert p.kind == "pdf" and p.pages == 3 and p.text_ratio == 1.0
    assert p.image_cover < 0.1 and p.has_table_lines and not p.math_hint and p.error is None


def test_scanned_pdf(fixtures):
    p = probe_file(fixtures / "scanned_cht.pdf")
    assert p.text_ratio == 0.0 and p.image_cover > 0.6 and p.blank_pages == []


def test_formula_pdf(fixtures):
    assert probe_file(fixtures / "formula.pdf").math_hint is True


def test_twocol_pdf(fixtures):
    assert probe_file(fixtures / "twocol.pdf").layout_hint is True
    assert probe_file(fixtures / "text.pdf").layout_hint is False


def test_blank_pdf(fixtures):
    p = probe_file(fixtures / "blank.pdf")
    assert p.pages == 1 and p.blank_pages == [1] and p.text_ratio == 0.0


def test_errors(fixtures):
    assert probe_file(fixtures / "encrypted.pdf").error == "encrypted"
    assert probe_file(fixtures / "corrupt.pdf").error == "corrupt"


def test_big_pdf_samples_at_most_20(fixtures, monkeypatch):
    seen = []
    import aidoc.probe as pr
    orig = pr._analyse_page
    monkeypatch.setattr(pr, "_analyse_page", lambda page: (seen.append(page.number), orig(page))[1])
    probe_file(fixtures / "big.pdf")
    assert len(seen) == 20 and seen[0] == 0 and seen[-1] == 44


def test_kinds(fixtures):
    assert probe_file(fixtures / "page.png").kind == "image"
    assert probe_file(fixtures / "sample.docx").kind == "office"
    assert probe_file(fixtures / "sample.html").kind == "html"
    assert probe_file(fixtures / "bad.exe").kind == "other"
    assert kind_for(".mp3") == "audio" and kind_for(".epub") == "other" and kind_for(".csv") == "other"


def test_blank_pages_counted_on_every_page_not_just_the_sample(tmp_path):
    """P1 verifier 3: blank pages from a <=20-page sample were subtracted from the full page count."""
    import pymupdf as fitz

    from aidoc.quality import assess
    doc = fitz.open()
    for i in range(45):
        page = doc.new_page()
        if i % 9 < 4:                                        # 20 text pages spread over the file, 25 blank
            page.insert_text((72, 72), f"Page {i + 1} " + "lorem ipsum dolor sit amet " * 3)
    p = tmp_path / "mixed.pdf"; doc.save(p); doc.close()
    pr = probe_file(p)
    assert pr.pages == 45 and len(pr.blank_pages) == 25
    assert pr.blank_pages == [i + 1 for i in range(45) if i % 9 >= 4]
    # every page carries its marker (spec 2026-10-01: blank pages too); only the text pages have text
    md = "\n".join(f"<!-- page: {i + 1} -->\n" + (f"Page {i + 1} " + "lorem ipsum dolor sit amet " * 3
                                                    if i % 9 < 4 else "")
                   for i in range(45))
    assert assess(md, pr, 45).level == "ok"


def test_broken_tounicode_fonts_detected(fixtures):
    p = probe_file(fixtures / "broken_tounicode.pdf")
    assert p.broken_font_pages == [1, 2] and len(p.broken_fonts) == 1


def test_normal_pdfs_have_no_broken_fonts(fixtures):
    for name in ("text.pdf", "big.pdf", "paged_furniture.pdf", "formula.pdf"):
        assert probe_file(fixtures / name).broken_font_pages == [], name


def test_broken_tounicode_text_layer_is_glyph_ids(fixtures):
    import pymupdf
    assert "dreamy" not in pymupdf.open(fixtures / "broken_tounicode.pdf")[0].get_text()


def test_paged_furniture_shape(fixtures):
    import pymupdf
    d = pymupdf.open(fixtures / "paged_furniture.pdf")
    assert d.page_count == 8 and d[3].get_text().strip() == "" and "........" in d[1].get_text()
    assert probe_file(fixtures / "paged_furniture.pdf").blank_pages == [4]


def test_table_lines_need_a_ruled_grid(tmp_path):
    """Figure call-out lines and filled boxes are not table rules (2026-10-01 acceptance, real orthopaedic book)."""
    import pymupdf as fitz
    doc = fitz.open()
    page = doc.new_page()
    sh = page.new_shape()
    for y in (300, 340, 380):                                  # call-out leaders: L shapes to three labels
        sh.draw_line((100, y), (250, y))
        sh.draw_line((250, y), (250, y - 30))
    sh.draw_rect(fitz.Rect(0, 0, 150, 40))                     # a coloured header band and a page-number box
    sh.draw_rect(fitz.Rect(350, 60, 560, 260))
    sh.finish(color=(0, 0, 0), fill=(0.5, 0.6, 0.8))
    sh.commit()
    page.insert_text((72, 500), "Body text " * 20)
    p = tmp_path / "callouts.pdf"
    doc.save(p)
    assert probe_file(p).has_table_lines is False
