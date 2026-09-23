"""Docling runner. STDLIB + docling only; never import aidoc."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _proto

_converter_cache: dict = {}


def build_converter(opts):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        AcceleratorOptions,
        EasyOcrOptions,
        OcrMode,
        PdfPipelineOptions,
    )
    from docling.datamodel.settings import settings
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
    models = Path(os.environ["AIDOC_MODELS_DIR"]) / "docling"
    mode = OcrMode.FULL_PAGE if opts.get("full_page_ocr") else None
    if opts.get("ocr") == "rapidocr":
        from docling.datamodel.pipeline_options import RapidOcrOptions
        ocr = RapidOcrOptions(lang=["chinese_cht"] if "ch_tra" in opts["lang"] else ["en"])
    else:
        ocr = EasyOcrOptions(lang=list(opts["lang"]))
    if mode is not None:
        ocr.mode = mode
    po = PdfPipelineOptions(artifacts_path=str(models), do_ocr=True, ocr_options=ocr, do_table_structure=True,
                            generate_picture_images=True, images_scale=2.0,
                            do_formula_enrichment=bool(opts.get("formula")),
                            accelerator_options=AcceleratorOptions(device="cuda"))
    settings.perf.page_batch_size = int(opts.get("page_batch_size", 16))
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=po),
                                             InputFormat.IMAGE: ImageFormatOption(pipeline_options=po)})


def table_edges(doc):
    """Return (first, last) TableEdge dicts using prov bboxes; None when no tables / no bbox.

    "touches_edge" is measured against the page's content (P2 verifier I2), not the physical page: the last table
    touches the bottom when no body item (page header/footer furniture excluded) ends below it on that page."""
    tables = [t for t in doc.tables if t.prov]
    if not tables:
        return None, None
    per_page = {}
    for item, _lvl in doc.iterate_items():
        if str(getattr(item, "label", "")).split(".")[-1].lower() in _proto.FURNITURE_TYPES:
            continue
        layer = str(getattr(item, "content_layer", "body")).split(".")[-1].lower()
        if layer not in ("body", ""):
            continue
        for prov in getattr(item, "prov", None) or []:
            h = doc.pages[prov.page_no].size.height
            bb = prov.bbox.to_top_left_origin(h) if hasattr(prov.bbox, "to_top_left_origin") else prov.bbox
            per_page.setdefault(prov.page_no, []).append((item, bb.t, bb.b))

    def edge(t, top):
        prov = t.prov[0]
        h = doc.pages[prov.page_no].size.height
        bb = prov.bbox.to_top_left_origin(h) if hasattr(prov.bbox, "to_top_left_origin") else prov.bbox
        others = [(a, b) for item, a, b in per_page.get(prov.page_no, []) if item is not t]
        touches = _proto.touches_content_edge((bb.t, bb.b), others, top=top, tol=0.005 * h)
        return {"page": prov.page_no, "n_cols": t.data.num_cols, "touches_edge": bool(touches)}
    return edge(tables[0], True), edge(tables[-1], False)


def _replace_placeholders(md, links):
    it = iter(links)

    def sub(m):
        try:
            return f"![]({next(it)})"
        except StopIteration:
            return ""
    return re.sub(r"<!-- image -->", sub, md)


def _page_count(doc):
    n = getattr(doc, "num_pages", None)
    return n() if callable(n) else len(doc.pages)


def handle(req):
    opts = req["engine_opts"]
    key = (opts.get("ocr"), tuple(opts["lang"]), bool(opts.get("full_page_ocr")),
           int(opts.get("page_batch_size", 16)), bool(opts.get("formula")))
    conv = _converter_cache.get(key)
    if conv is None:
        conv = _converter_cache.setdefault(key, build_converter(opts))
    out = Path(req["out_dir"])
    (out / "images").mkdir(parents=True, exist_ok=True)
    res = conv.convert(req["src"], raises_on_error=False)
    status = str(getattr(res, "status", "")).lower()
    if "failure" in status and res.document is None:
        raise _proto.RunnerError("engine", f"docling conversion failed: {getattr(res, 'errors', '')}")
    doc = res.document
    n = _page_count(doc)
    paged = req.get("kind", "pdf") == "pdf"          # images are a single unnumbered page: no markers
    parts, images = [], []
    for p in range(1, n + 1):
        md = doc.export_to_markdown(page_no=p)
        pics = [pic for pic in doc.pictures if pic.prov and pic.prov[0].page_no == p]
        links = []
        for k, pic in enumerate(pics, 1):
            img = pic.get_image(doc)
            if img is None:
                continue
            f = out / "images" / f"page_{p}_{k}.png"
            img.save(f)
            images.append(str(f))
            links.append(f"images/{f.name}")
        md = _replace_placeholders(md, links)
        parts.append(f"<!-- page: {p} -->\n{md.strip()}\n" if paged else f"{md.strip()}\n")
        _proto.progress(p, n)
    first, last = table_edges(doc)
    md_path = out / "out.md"
    md_path.write_text("\n".join(parts), encoding="utf-8")
    return {"markdown_path": str(md_path), "images": images, "has_page_markers": paged, "page_count": n,
            "first_table": first, "last_table": last}


if __name__ == "__main__":
    import docling.document_converter  # noqa: F401  (import before READY; timeout clock excludes it)
    _proto.serve(handle)
