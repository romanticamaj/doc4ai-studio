from __future__ import annotations

import math
from pathlib import Path

from doc4ai.engines.base import RunnerEngine
from doc4ai.models import ConvertOptions, ProbeResult

# T-003: Docling's OCR stage renders each page at scale 3 (216 dpi; its picture images use images_scale 2). Pillow
# refuses an image above 2 × Image.MAX_IMAGE_PIXELS as a decompression bomb, so a 6253×4428 pt spread (249 M px)
# came out empty. Oversized pages are scaled down in a copy sent to Docling; the bomb check is never disabled.
PILLOW_BOMB_ERROR_PX = 178_956_970          # = 2 × PIL.Image.MAX_IMAGE_PIXELS (89,478,485), Pillow's default
DOCLING_MAX_RENDER_SCALE = 3.0
RENDER_PX_CAP = int(PILLOW_BOMB_ERROR_PX * 0.9)   # a margin for rounding in the renderers


def cap_page_size(src: Path, workdir: Path) -> tuple[Path, dict]:
    """(pdf for Docling, info): the original when every page renders under RENDER_PX_CAP at scale 3, else a copy in
    `workdir` where each oversized page is scaled (vector content and aspect kept) to fit; other pages copied as is."""
    import pymupdf
    src = Path(src)
    info: dict = {"scaled_pages": [], "factor": {}}
    with pymupdf.open(src) as doc:
        factors = {}
        for i, page in enumerate(doc, 1):
            px = page.rect.width * page.rect.height * DOCLING_MAX_RENDER_SCALE ** 2
            if px > RENDER_PX_CAP:
                factors[i] = math.sqrt(RENDER_PX_CAP / px) * 0.999
        if not factors:
            return src, info
        out = Path(workdir) / f"{src.stem}.render-capped.pdf"
        out.parent.mkdir(parents=True, exist_ok=True)
        with pymupdf.open() as dst:
            for i, page in enumerate(doc, 1):
                f = factors.get(i)
                if f is None:
                    dst.insert_pdf(doc, from_page=i - 1, to_page=i - 1)
                    continue
                r = page.rect
                new = dst.new_page(width=r.width * f, height=r.height * f)
                new.show_pdf_page(new.rect, doc, i - 1)
            dst.save(out, garbage=3, deflate=True)
    info.update(scaled_pages=sorted(factors), factor={i: round(f, 4) for i, f in factors.items()})
    return out, info


def is_scanned(probe: ProbeResult) -> bool:
    return probe.kind == "image" or (probe.kind == "pdf" and (probe.text_ratio < 0.5 or probe.image_cover > 0.6))


class DoclingEngine(RunnerEngine):
    name = "docling"

    def engine_opts(self, opts: ConvertOptions, probe: ProbeResult, full_page_ocr: bool = False) -> dict:
        return {"ocr": opts.docling_ocr,
                "lang": ["ch_tra", "en"] if opts.lang == "cht" else ["en"],
                "full_page_ocr": bool(full_page_ocr or is_scanned(probe)),
                "page_batch_size": int(self.config.engines.docling_page_batch_size),
                "formula": bool(probe.math_hint)}

    def build_request(self, src: Path, workdir: Path, opts: ConvertOptions, probe: ProbeResult,
                      pages: tuple[int, int] | None, engine_opts: dict, segment_idx: int = 0) -> dict:
        req = super().build_request(src, workdir, opts, probe, pages, engine_opts, segment_idx)
        if probe.kind == "pdf" and Path(src).suffix.lower() == ".pdf":
            capped, info = cap_page_size(Path(src), Path(workdir))
            if info["scaled_pages"]:
                req["src"] = str(capped)
                req["render_cap"] = info
        return req

    def oom_downgrade(self, engine_opts: dict) -> dict | None:
        size = int(engine_opts.get("page_batch_size", 16))
        if size <= 1:
            return None
        return {**engine_opts, "page_batch_size": max(1, size // 2)}
