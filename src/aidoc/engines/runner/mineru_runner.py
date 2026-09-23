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
import _proto  # noqa: E402

_IMG_LINK = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


def _needles(item):
    """Candidate substrings (longest first) that locate this item in the rendered markdown."""
    t = item.get("type")
    img = item.get("img_path") or item.get("image_source")
    if t in ("image", "chart", "figure") and isinstance(img, str) and not img.startswith("data:"):
        return [os.path.basename(img)]
    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        text = item.get("content") if isinstance(item.get("content"), str) else ""
    text = text.strip()
    if text:
        first_line = text.splitlines()[0].strip()
        cands = [first_line[:40], first_line[:15], first_line[:6]]
        return [c for i, c in enumerate(cands) if c and c not in cands[:i]]
    if t == "table":
        return ["<table"]
    return []


def insert_page_markers(md, items):
    """Insert `<!-- page: N -->` before the first element of every page. None when a page cannot be located."""
    by_page: dict[int, list] = {}
    order: list[int] = []
    for it in items:
        page = it.get("page_idx")
        if page is None:
            continue
        if page not in by_page:
            by_page[page] = []
            order.append(page)
        by_page[page].append(it)
    if not order:
        return None
    cursor = 0
    cuts: list[tuple[int, int]] = []
    for page in order:
        found = None
        for it in by_page[page]:
            for needle in _needles(it):
                pos = md.find(needle, cursor)
                if pos >= 0:
                    found = (pos, needle)
                    break
            if found:
                break
        if found is None:
            if all(not _needles(it) for it in by_page[page]):
                continue                         # page with nothing locatable (e.g. empty page): no marker
            return None
        pos, needle = found
        line_start = md.rfind("\n", 0, pos) + 1
        if cuts and line_start < cuts[-1][1]:
            line_start = cuts[-1][1]
        cuts.append((page, line_start))
        cursor = pos + len(needle)
    if not cuts:
        return None
    out, prev = [], 0
    for page, at in cuts:
        out.append(md[prev:at])
        out.append(f"<!-- page: {page + 1} -->\n")
        prev = at
    out.append(md[prev:])
    return "".join(out)


def flatten_structured(sc):
    """structured_content.json -> flat item list with page_idx (MinerU 4 groups blocks per page)."""
    if isinstance(sc, list):
        return sc
    if isinstance(sc, dict) and "pages" in sc:
        items = []
        for k, p in enumerate(sc["pages"]):
            for i in p.get("blocks", p.get("items", p.get("content", []))) or []:
                if isinstance(i, dict):
                    items.append(dict(i, page_idx=p.get("page_idx", k)))
        return items
    return []


def _html_cols(html):
    if not html:
        return 0
    first_row = re.search(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I)
    return len(re.findall(r"<t[dh]\b", first_row.group(1) if first_row else html, re.I))


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
    """(first, last) TableEdge dicts from middle_json (MinerU 4: normalised [0..1] bbox per block)."""
    try:
        pages = middle["pages"] if isinstance(middle, dict) else middle
        tabs = []
        for k, pg in enumerate(pages):
            page_idx = pg.get("page_idx", k)
            blocks = pg.get("blocks", pg.get("para_blocks", []))
            size = pg.get("page_size")
            for blk in blocks:
                if blk.get("type") != "table" or not blk.get("bbox"):
                    continue
                x0, y0, x1, y1 = blk["bbox"]
                if size and max(y0, y1) > 1.5:          # absolute coordinates (older layouts)
                    y0, y1 = y0 / size[1], y1 / size[1]
                html = _block_html(blk)
                ncols = _html_cols(html) or _gfm_cols(blk.get("content"))
                tabs.append({"page": page_idx + 1, "top": y0, "bottom": y1, "n_cols": ncols})
        if not tabs:
            return None, None
        f, last = tabs[0], tabs[-1]
        return ({"page": f["page"], "n_cols": f["n_cols"], "touches_edge": f["top"] <= 0.05},
                {"page": last["page"], "n_cols": last["n_cols"], "touches_edge": last["bottom"] >= 0.95})
    except Exception:  # noqa: BLE001  structure differs -> no edge info
        return None, None


def handle(req):
    from mineru.parser import parse
    from mineru.parser.writer import FileBasedDataWriter
    out = Path(req["out_dir"])
    base = out / "mineru"
    base.mkdir(parents=True, exist_ok=True)
    tier = req["engine_opts"].get("tier", "basic")
    result = parse(req["src"], tier=tier, ocr_mode="auto")
    result.save(FileBasedDataWriter(str(base)))
    md = (base / "markdown.md").read_text(encoding="utf-8")
    sc = json.loads((base / "structured_content.json").read_text(encoding="utf-8"))
    items = flatten_structured(sc)
    # page markers only make sense for paged input (PDF); an image is a single unnumbered page
    marked = insert_page_markers(md, items) if req.get("kind", "pdf") == "pdf" else None
    has_pages = marked is not None
    md = marked or md
    referenced = {os.path.basename(m.group(1)) for m in _IMG_LINK.finditer(md)}
    images = [str(p) for p in sorted((base / "images").glob("*")) if p.is_file() and p.name in referenced]
    mj = base / "middle_json.json"
    middle = json.loads(mj.read_text(encoding="utf-8")) if mj.exists() else {}
    first, last = table_edges(middle)
    total = None
    if isinstance(sc, dict):
        total = ((sc.get("metadata") or {}).get("document") or {}).get("page_count") or len(sc.get("pages") or []) or None
    md_path = out / "out.md"
    md_path.write_text(md, encoding="utf-8")
    _proto.progress(total or 1, total or 1)
    return {"markdown_path": str(md_path), "images": images, "has_page_markers": has_pages, "page_count": total,
            "first_table": first, "last_table": last}


if __name__ == "__main__":
    import mineru.parser  # noqa: F401  (import before READY; timeout clock excludes it)
    _proto.serve(handle)
