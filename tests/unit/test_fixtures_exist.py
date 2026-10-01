import fitz

from tests.conftest import FIXTURES


def test_pdf_page_counts():
    assert fitz.open(FIXTURES / "text.pdf").page_count == 3
    assert fitz.open(FIXTURES / "big.pdf").page_count == 45
    assert fitz.open(FIXTURES / "scanned_cht.pdf")[0].get_text().strip() == ""
    assert fitz.open(FIXTURES / "encrypted.pdf").is_encrypted


def test_office_and_misc_exist():
    for n in ["sample.docx", "sample.pptx", "sample.xlsx", "sample.html", "page.png", "bad.exe", "corrupt.pdf",
              "paged_furniture.pdf", "broken_tounicode.pdf"]:
        assert (FIXTURES / n).stat().st_size > 0
