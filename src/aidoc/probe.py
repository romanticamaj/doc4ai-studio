"""File probing (spec §4).

Final thresholds (validated on tests/fixtures):
- has_text: >= 20 chars of extracted text on the page
- table lines: >= 3 horizontal and >= 2 vertical ruling lines
- two-column: >= 8 narrow (< 0.6 W) text blocks, >= 35 % centred left of 0.45 W and >= 35 % right of 0.55 W
- math: math font names, or >= 15 math symbols and >= 1 % of non-space chars
"""
from __future__ import annotations

from pathlib import Path

import pymupdf as fitz

from aidoc.models import InputKind, ProbeResult

MAX_SAMPLE_PAGES = 20

IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"})
OFFICE_EXTS = frozenset({".docx", ".pptx", ".xlsx", ".xls", ".doc", ".ppt"})
HTML_EXTS = frozenset({".html", ".htm"})
AUDIO_EXTS = frozenset({".mp3", ".wav", ".m4a"})
MARKITDOWN_EXTS = OFFICE_EXTS | HTML_EXTS | AUDIO_EXTS | frozenset(
    {".csv", ".json", ".xml", ".txt", ".md", ".epub", ".zip", ".ipynb", ".msg", ".rss", ".atom"})

_MATH_FONTS = ("CMMI", "CMSY", "CMEX", "MSAM", "MSBM", "SYMBOL", "CAMBRIAMATH", "CAMBRIA-MATH",
               "STIXMATH", "LATINMODERNMATH", "XITSMATH")
_MATH_SYMS = set("∑∫∂√∞≈≠≤≥±×÷∈∀∃∇∏αβγδεζηθλμπσφψωΩ")


def kind_for(ext: str) -> InputKind:
    ext = ext.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in OFFICE_EXTS:
        return "office"
    if ext in HTML_EXTS:
        return "html"
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"


def sample_indices(n: int, k_max: int = MAX_SAMPLE_PAGES) -> list[int]:
    if n <= 0:
        return []
    k = min(n, k_max)
    if k == 1:
        return [0]
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def _is_blank(page) -> bool:
    """Cheap exact check run on every page: no text, no image, no vector drawing."""
    if len(page.get_text("text").strip()) >= 3:
        return False
    return not page.get_image_info() and not page.get_drawings()


def _analyse_page(page) -> dict:
    rect = page.rect
    W, H = rect.width, rect.height
    area = max(1.0, W * H)
    text = page.get_text("text")
    stripped = text.strip()
    has_text = len(stripped) >= 20

    img_area = 0.0
    infos = page.get_image_info()
    for info in infos:
        r = fitz.Rect(info["bbox"]) & rect
        if not r.is_empty:
            img_area += r.width * r.height
    image_cover = min(1.0, img_area / area)

    fonts = [str(f[3]).upper() for f in page.get_fonts()]
    math_fonts = any(m in f for f in fonts for m in _MATH_FONTS)
    non_space = [c for c in text if not c.isspace()]
    n_sym = sum(1 for c in non_space if c in _MATH_SYMS)
    math_syms = n_sym >= 15 and n_sym >= 0.01 * max(1, len(non_space))

    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and (b[2] - b[0]) < 0.6 * W]
    two_col = False
    if len(blocks) >= 8:
        left = sum(1 for b in blocks if (b[0] + b[2]) / 2 < 0.45 * W)
        right = sum(1 for b in blocks if (b[0] + b[2]) / 2 > 0.55 * W)
        two_col = left >= 0.35 * len(blocks) and right >= 0.35 * len(blocks)

    drawings = page.get_drawings()
    h = v = 0

    def classify(p1, p2):
        nonlocal h, v
        dx, dy = abs(p2.x - p1.x), abs(p2.y - p1.y)
        if dy < 1 and dx >= 0.05 * W:
            h += 1
        elif dx < 1 and dy >= 0.02 * H:
            v += 1

    for d in drawings:
        for item in d.get("items", []):
            if item[0] == "l":
                classify(item[1], item[2])
            elif item[0] == "re":
                r = item[1]
                classify(r.tl, r.tr)
                classify(r.bl, r.br)
                classify(r.tl, r.bl)
                classify(r.tr, r.br)
    table_lines = h >= 3 and v >= 2
    blank = len(stripped) < 3 and not infos and not drawings
    return {"has_text": has_text, "image_cover": image_cover, "math": math_fonts or math_syms,
            "two_col": two_col, "table_lines": table_lines, "blank": blank}


def _base_name(name: str) -> str:
    """Font name without the subset prefix (`ABCDEF+ComicSansMS` -> `ComicSansMS`)."""
    if len(name) > 7 and name[6] == "+" and name[:6].isalpha() and name[:6].isupper():
        return name[7:]
    return name


def broken_fonts(doc) -> tuple[list[str], list[int]]:
    """Fonts whose text cannot be mapped to Unicode (spec 2026-10-01 §5.5): type `Type0` or an `Identity`
    encoding, and no `/ToUnicode` in the font dictionary. Scans every page; returns (sorted unique names,
    sorted 1-based pages that use one). Results are cached per font xref."""
    cache: dict[int, bool] = {}
    names: set[str] = set()
    pages: list[int] = []
    for i in range(doc.page_count):
        hit = False
        for f in doc[i].get_fonts():
            xref, ftype, basefont, enc = f[0], str(f[2]), str(f[3]), str(f[5])
            if xref not in cache:
                broken = False
                if ftype == "Type0" or "Identity" in enc:
                    try:
                        broken = doc.xref_get_key(xref, "ToUnicode")[0] == "null"
                    except Exception:  # noqa: BLE001  a damaged font object is not our signal
                        broken = False
                cache[xref] = broken
            if cache[xref]:
                hit = True
                names.add(_base_name(basefont))
        if hit:
            pages.append(i + 1)
    return sorted(names), pages


def probe_file(path: Path) -> ProbeResult:
    path = Path(path)
    ext = path.suffix.lower()
    size = path.stat().st_size
    kind = kind_for(ext)
    if kind != "pdf":
        return ProbeResult(kind=kind, ext=ext, size=size)
    try:
        doc = fitz.open(path)
    except Exception:  # noqa: BLE001  PyMuPDF raises many types for broken files
        return ProbeResult(kind="pdf", ext=ext, size=size, error="corrupt")
    try:
        if doc.is_encrypted and not doc.authenticate(""):
            return ProbeResult(kind="pdf", ext=ext, size=size, error="encrypted")
        n = doc.page_count
        if n == 0:
            return ProbeResult(kind="pdf", ext=ext, size=size, pages=0, error="corrupt")
        stats = [_analyse_page(doc[i]) for i in sample_indices(n)]
        blank_pages = [i + 1 for i in range(n) if _is_blank(doc[i])]   # every page: quality divides by pages
        non_blank = [m for m in stats if not m["blank"]]
        text_ratio = (sum(1 for m in non_blank if m["has_text"]) / len(non_blank)) if non_blank else 0.0
        image_cover = sum(m["image_cover"] for m in stats) / len(stats) if stats else 0.0
        layout = bool(non_blank) and sum(1 for m in non_blank if m["two_col"]) >= len(non_blank) / 2
        bf_names, bf_pages = broken_fonts(doc)
        return ProbeResult(kind="pdf", ext=ext, size=size, pages=n, text_ratio=text_ratio,
                           image_cover=image_cover, math_hint=any(m["math"] for m in stats),
                           layout_hint=layout, has_table_lines=any(m["table_lines"] for m in stats),
                           blank_pages=blank_pages, broken_fonts=bf_names, broken_font_pages=bf_pages)
    except Exception:  # noqa: BLE001  PyMuPDF raises many types for broken files
        return ProbeResult(kind="pdf", ext=ext, size=size, error="corrupt")
    finally:
        doc.close()
