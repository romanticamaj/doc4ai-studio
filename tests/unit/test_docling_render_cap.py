"""T-003: Docling renders a page at up to scale 3 (its OCR stage, 216 dpi); a 6253×4428 pt spread becomes 249 M px
and Pillow's decompression-bomb check rejects every page. The Docling request gets a copy whose oversized pages are
scaled down so the largest render stays under the limit; the bomb check itself is never disabled."""
from __future__ import annotations

from pathlib import Path

import pymupdf

from aidoc.engines import docling as D
from aidoc.models import ConvertOptions, ProbeResult

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _pdf(path: Path, sizes: list[tuple[float, float]]) -> Path:
    doc = pymupdf.open()
    for w, h in sizes:
        page = doc.new_page(width=w, height=h)
        page.insert_text((72, 72), f"page {w:.0f}x{h:.0f}", fontsize=48)
        page.draw_rect(pymupdf.Rect(10, 10, w - 10, h - 10), color=(0, 0, 0), width=4)
    doc.save(path)
    doc.close()
    return path


def _pixels(w: float, h: float) -> float:
    return w * h * D.DOCLING_MAX_RENDER_SCALE ** 2


def test_limit_is_pillows_error_threshold_with_a_margin():
    assert D.PILLOW_BOMB_ERROR_PX == 178_956_970            # PIL: 2 × Image.MAX_IMAGE_PIXELS (89,478,485)
    assert D.DOCLING_MAX_RENDER_SCALE == 3.0
    assert D.RENDER_PX_CAP < D.PILLOW_BOMB_ERROR_PX


def test_a_huge_page_is_scaled_under_the_cap(tmp_path):
    src = _pdf(tmp_path / "big.pdf", [(6000, 4400), (612, 792)])
    assert _pixels(6000, 4400) > D.PILLOW_BOMB_ERROR_PX      # the failure the run hit
    out, info = D.cap_page_size(src, tmp_path / "work")
    assert out != src and out.is_file()
    with pymupdf.open(out) as d:
        assert d.page_count == 2
        big, small = d[0].rect, d[1].rect
        assert _pixels(big.width, big.height) <= D.RENDER_PX_CAP
        assert abs(big.width / big.height - 6000 / 4400) < 1e-3                 # aspect kept
        assert (round(small.width), round(small.height)) == (612, 792)          # a normal page is untouched
        assert "page 6000x4400" in d[0].get_text()                              # content kept (vector, scaled)
    assert info["scaled_pages"] == [1] and 0.5 < info["factor"][1] < 1.0


def test_normal_pages_are_not_copied(tmp_path):
    src = _pdf(tmp_path / "a4.pdf", [(595, 842)] * 3)
    out, info = D.cap_page_size(src, tmp_path / "work")
    assert out == src and info["scaled_pages"] == []


def test_build_request_sends_the_capped_copy(tmp_path):
    src = _pdf(tmp_path / "spread.pdf", [(6254, 4428)])
    eng = D.DoclingEngine.__new__(D.DoclingEngine)
    probe = ProbeResult(kind="pdf", ext=".pdf", size=1, pages=1, text_ratio=0.0, image_cover=1.0, math_hint=False)
    req = eng.build_request(src, tmp_path / "w", ConvertOptions(output_dir=tmp_path), probe, None, {"ocr": "rapidocr"})
    assert Path(req["src"]) != src and req["render_cap"]["scaled_pages"] == [1]
    with pymupdf.open(req["src"]) as d:
        assert _pixels(d[0].rect.width, d[0].rect.height) <= D.RENDER_PX_CAP


def test_images_and_non_pdf_requests_are_left_alone(tmp_path):
    eng = D.DoclingEngine.__new__(D.DoclingEngine)
    probe = ProbeResult(kind="image", ext=".png", size=1, pages=1, text_ratio=0.0, image_cover=1.0, math_hint=False)
    req = eng.build_request(FIX / "page.png", tmp_path / "w", ConvertOptions(output_dir=tmp_path), probe, None, {})
    assert req["src"] == str(FIX / "page.png") and "render_cap" not in req
