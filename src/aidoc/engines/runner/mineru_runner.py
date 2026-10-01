"""MinerU 4.x runner. STDLIB + mineru only; never import aidoc.

API (verified in spike C, mineru 4.0.7): mineru.parser.parse(path, *, tier, ocr_mode, image_analysis, page_range)
-> ParseResult; ParseResult.save(writer: DataWriter) with mineru.parser.writer.FileBasedDataWriter(parent_dir)
writes markdown.md, middle_json.json, structured_content.json, model_output.json and images/.
There is no language parameter (spec §4): `lang` is ignored.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _proto

_IMG_LINK = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


def _plan_pages(middle_json):
    """MinerU's own Markdown renderer, page by page (spec 2026-10-01 §3.2 method P): the DEFAULT render plan of
    docvortex, each page rendered with the same arguments `render_markdown()` uses. Internal API, locked by
    envs/mineru/uv.lock; `render_pages` verifies the result byte for byte."""
    from docvortex.render._internal.common.planner import build_render_plan
    from docvortex.render._internal.markdown import renderer as R
    from docvortex.render.contracts import RenderMode
    from mineru.config import config
    planned = build_render_plan(middle_json, RenderMode.DEFAULT)
    targets = R._collect_markdown_anchor_targets(middle_json)
    emitted: set = set()
    return [R._render_page(p, mode=RenderMode.DEFAULT, delimiters=config.render.latex_delimiters, asset_base_url="",
                           image_renderer=None, anchor_targets=targets, emitted_anchors=emitted) for p in planned]


def _single_pages(middle_json):
    """Public API fallback: render a one-page MiddleJson per page (100 % coverage, no cross-page merges)."""
    from mineru.render import render_markdown
    return [render_markdown(middle_json.model_copy(update={"pages": [pg]})) for pg in middle_json.pages]


def render_pages(middle_json, *, reference, plan=None, single=None):
    """(pages, method): the render plan when its pages, joined like MinerU joins them, equal markdown.md byte for
    byte; otherwise (mismatch or any error) the public single-page render."""
    plan = plan or _plan_pages
    single = single or _single_pages
    try:
        pages = plan(middle_json)
        if "\n\n".join(p for p in pages if p) == reference:
            return pages, "mineru_render_plan"
        reason = "per-page render differs from markdown.md"
    except Exception as e:  # noqa: BLE001  internal API moved / changed: fall back, never lose page markers
        reason = f"{type(e).__name__}: {e}"
    _proto.log(f"mineru page map: render plan not usable ({reason}); using single-page render")
    return single(middle_json), "mineru_single_page"


def assemble(pages):
    """Every page gets its marker, blank pages included."""
    return "".join(f"<!-- page: {i} -->\n\n" + (p.strip("\n") + "\n\n" if p.strip() else "")
                   for i, p in enumerate(pages, 1))


def _html_cols(html):
    if not html:
        return 0
    first_row = re.search(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)
    return len(re.findall(r"<t[dh]\b", first_row.group(1) if first_row else html, re.IGNORECASE))


def _block_html(blk):
    content = blk.get("content")
    if isinstance(content, str):
        return content if "<t" in content else ""
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict):
                h = _block_html(c)
                if h:
                    return h
    return blk.get("html") or ""


def _gfm_cols(text):
    if not isinstance(text, str) or not text.strip().startswith("|"):
        return 0
    return max(0, text.strip().splitlines()[0].strip().strip("|").count("|") + 1)


def table_edges(middle):
    """(first, last) TableEdge dicts from middle_json (MinerU 4: normalised [0..1] bbox per block).

    "touches_edge" is measured against the page's content (P2 verifier I2): the last table touches the bottom when
    no non-furniture block ends below it; the first table touches the top when none starts above it."""
    try:
        pages = middle["pages"] if isinstance(middle, dict) else middle
        tabs = []
        for k, pg in enumerate(pages):
            page_idx = pg.get("page_idx", k)
            blocks = pg.get("blocks", pg.get("para_blocks", []))
            size = pg.get("page_size")
            spans = []
            for blk in blocks:
                if not blk.get("bbox") or blk.get("type") in _proto.FURNITURE_TYPES:
                    continue
                _x0, y0, _x1, y1 = blk["bbox"]
                if size and max(y0, y1) > 1.5:          # absolute coordinates (older layouts)
                    y0, y1 = y0 / size[1], y1 / size[1]
                spans.append((blk, y0, y1))
            for blk, y0, y1 in spans:
                if blk.get("type") != "table":
                    continue
                others = [(a, b) for other, a, b in spans if other is not blk]
                html = _block_html(blk)
                ncols = _html_cols(html) or _gfm_cols(blk.get("content"))
                tabs.append({"page": page_idx + 1, "n_cols": ncols,
                             "top_edge": _proto.touches_content_edge((y0, y1), others, top=True),
                             "bottom_edge": _proto.touches_content_edge((y0, y1), others, top=False)})
        if not tabs:
            return None, None
        f, last = tabs[0], tabs[-1]
        return ({"page": f["page"], "n_cols": f["n_cols"], "touches_edge": f["top_edge"]},
                {"page": last["page"], "n_cols": last["n_cols"], "touches_edge": last["bottom_edge"]})
    except Exception:  # noqa: BLE001  structure differs -> no edge info
        return None, None


def handle(req):
    from mineru.parser import ParseResult, parse
    from mineru.parser.writer import FileBasedDataWriter
    out = Path(req["out_dir"])
    base = out / "mineru"
    base.mkdir(parents=True, exist_ok=True)
    tier = req["engine_opts"].get("tier", "basic")
    ocr_mode = req["engine_opts"].get("ocr_mode", "auto")
    result = parse(req["src"], tier=tier, ocr_mode=ocr_mode)
    result.save(FileBasedDataWriter(str(base)))
    md = (base / "markdown.md").read_text(encoding="utf-8")
    mj = base / "middle_json.json"
    mj_text = mj.read_text(encoding="utf-8") if mj.exists() else ""
    middle = json.loads(mj_text) if mj_text else {}
    total = ((middle.get("metadata") or {}).get("document") or {}).get("page_count") \
        or len(middle.get("pages") or []) or None
    method = None
    has_pages = False
    # page markers only make sense for paged input (PDF); an image is a single unnumbered page
    if req.get("kind", "pdf") == "pdf" and mj_text:
        pr = ParseResult.from_json(mj_text)            # the saved version: image paths match markdown.md
        pages, method = render_pages(pr.middle_json, reference=md)
        md = assemble(pages)
        has_pages = True
        total = total or len(pages)
    referenced = {os.path.basename(m.group(1)) for m in _IMG_LINK.finditer(md)}
    images = [str(p) for p in sorted((base / "images").glob("*")) if p.is_file() and p.name in referenced]
    first, last = table_edges(middle)
    md_path = out / "out.md"
    md_path.write_text(md, encoding="utf-8")
    _proto.progress(total or 1, total or 1)
    return {"markdown_path": str(md_path), "images": images, "has_page_markers": has_pages, "page_count": total,
            "first_table": first, "last_table": last, "page_map_method": method}


if __name__ == "__main__":
    import mineru.parser  # noqa: F401  (import before READY; timeout clock excludes it)
    _proto.serve(handle)
