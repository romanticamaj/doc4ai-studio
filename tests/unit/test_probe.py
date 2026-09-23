from aidoc.probe import probe_file, kind_for


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
