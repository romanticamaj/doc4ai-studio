import json
import re
import shutil

import pytest

from aidoc.cli import main

pytestmark = pytest.mark.slow


def _runner_pids(capsys_err: str) -> list[str]:
    return re.findall(r"runner started pid=(\d+)", capsys_err)


def test_big_pdf_segmented_with_docling(tmp_root, fixtures, capsys):
    src = tmp_root / "big.pdf"; shutil.copy(fixtures / "big.pdf", src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--json", "-v"]) == 0
    err = capsys.readouterr().err
    sc = json.loads((tmp_root / "out" / "big" / "big.json").read_text(encoding="utf-8"))
    assert sc["segments"] == 2 and sc["pages"] == 45 and sc["quality"]["level"] == "ok" and sc["engine"] == "docling"
    md = (tmp_root / "out" / "big" / "big.md").read_text(encoding="utf-8")
    # Docling classifies the page-top line "第 N 頁 / Page N" as page-header furniture and drops it on every page
    # (P1 behaviour too), so check that each page's body text arrived, including all of segment 2.
    assert "<!-- page: 41 -->" in md and md.count("<!-- page: ") == 45
    assert md.count("This is an English paragraph") == 45 and md.index("<!-- page: 45 -->") < md.rindex("English")
    assert len(_runner_pids(err)) == 1                        # one runner process served both segments


def test_big_pdf_segmented_with_mineru_forced(tmp_root, fixtures, capsys):
    src = tmp_root / "big.pdf"; shutil.copy(fixtures / "big.pdf", src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--engine", "mineru", "--json", "-v"]) == 0
    err = capsys.readouterr().err
    sc = json.loads((tmp_root / "out" / "big" / "big.json").read_text(encoding="utf-8"))
    assert sc["segments"] == 2 and sc["quality"]["level"] == "ok"
    md = (tmp_root / "out" / "big" / "big.md").read_text(encoding="utf-8")
    assert "<!-- page: 41 -->" in md and "第 45 頁" in md
    assert [int(n) for n in re.findall(r"<!-- page: (\d+) -->", md)] == list(range(1, 46))
    assert len(_runner_pids(err)) == 1


def _make_scanned_book(path, n=45, blank=(5, 43)):
    """Image-only book pages: running title, "Chapter k" line, body, page-number footer; some pages blank."""
    import fitz
    font = fitz.Font("helv")
    out = fitz.open()
    for k in range(1, n + 1):
        src = fitz.open()
        page = src.new_page()
        if k not in blank:
            tw = fitz.TextWriter(page.rect)
            for y, text in ((72, "A System of Orthopaedic Medicine"), (110, f"Chapter {k}: The knee {k}"),
                            (150, "This is an English paragraph used to test the document conversion pipeline."),
                            (170, f"It contains enough words to exceed the per-page threshold. Item {k}.")):
                tw.append((72, y), text, font=font, fontsize=14)
            tw.append((page.rect.width / 2 - 10, page.rect.height - 36), str(k), font=font, fontsize=12)
            tw.write_text(page)
        png = page.get_pixmap(dpi=120).tobytes("png")
        src.close()
        dst = out.new_page()
        dst.insert_image(dst.rect, stream=png)
    out.save(path, garbage=4, deflate=True)
    out.close()


def test_scanned_book_segmented_with_mineru_has_one_marker_per_page(tmp_root, capsys):
    """Regression: MinerU emitted one marker per 40-page segment (pages 1, 41, ...) because the page-number footer's
    needle hijacked the page mapping. Every page, blank ones included, must have exactly one marker, in order."""
    src = tmp_root / "book.pdf"
    _make_scanned_book(src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--engine", "mineru", "--json"]) == 0
    capsys.readouterr()
    sc = json.loads((tmp_root / "out" / "book" / "book.json").read_text(encoding="utf-8"))
    assert sc["segments"] == 2 and sc["pages"] == 45
    md = (tmp_root / "out" / "book" / "book.md").read_text(encoding="utf-8")
    assert [int(n) for n in re.findall(r"<!-- page: (\d+) -->", md)] == list(range(1, 46))
    for k in range(1, 46):
        body = md.split(f"<!-- page: {k} -->", 1)[1].split("<!-- page: ", 1)[0]
        assert re.findall(r"Chapter (\d+):", body) == ([] if k in (5, 43) else [str(k)]), (k, body[:200])


def _span_table_joined(md: str) -> bool:
    """The A-rows (page 40) and B-rows (page 41) form one GFM table: no non-table line between them."""
    lines = md.splitlines()
    first_a = next(i for i, ln in enumerate(lines) if ln.startswith("|") and "A0C0" in ln)
    last_b = max(i for i, ln in enumerate(lines) if ln.startswith("|") and "B4C" in ln)
    return all(ln.startswith("|") for ln in lines[first_a:last_b + 1])


@pytest.mark.parametrize("engine", ["docling", "mineru"])
def test_table_across_segment_boundary_with_real_margins_is_joined(tmp_root, fixtures, engine):
    """P2 verifier I2: 1-inch margins + a page number footer; the table crossing pages 40/41 must be one table."""
    src = tmp_root / "span_margin.pdf"; shutil.copy(fixtures / "span_margin.pdf", src)
    assert main(["convert", str(src), "-o", str(tmp_root / "out"), "--engine", engine, "--json"]) == 0
    md = (tmp_root / "out" / "span_margin" / "span_margin.md").read_text(encoding="utf-8")
    assert "A0C0" in md and "B4C2" in md
    assert _span_table_joined(md), md[md.find("A20C0") - 200: md.find("B4C2") + 200]
