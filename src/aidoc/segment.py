"""PDF segmentation (spec §8.3): plan 40-page segments, split physically with PyMuPDF, merge outputs."""
from __future__ import annotations

from pathlib import Path

import fitz

from aidoc.models import SEGMENT_PAGES


def plan_segments(pages: int | None) -> list[tuple[int | None, int | None]]:
    """1-based inclusive page ranges; page-less inputs are one segment (None, None)."""
    if not pages:
        return [(None, None)]
    return [(a, min(a + SEGMENT_PAGES - 1, pages)) for a in range(1, pages + 1, SEGMENT_PAGES)]


def split_pdf(src: Path, page_start: int, page_end: int, dst: Path) -> Path:
    """Write pages page_start..page_end (1-based, inclusive) of src into dst."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.tmp")
    with fitz.open(str(src)) as doc, fitz.open() as out:
        out.insert_pdf(doc, from_page=page_start - 1, to_page=page_end - 1)
        out.save(str(tmp), garbage=3, deflate=True)
    tmp.replace(dst)
    return dst


def segment_dir(work_dir: Path, idx: int) -> Path:
    return Path(work_dir) / f"seg_{idx}"
