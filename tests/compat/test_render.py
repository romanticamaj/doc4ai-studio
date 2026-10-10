from aidoc.compat.options import CompatOptions
from tests.mcp.conftest import TINY_PNG, page_md

BASE = {"to_formats": ["md"], "unsupported_formats": [], "placeholder": "", "keep_page_markers": False,
        "image_mode": "placeholder", "lang": "cht", "engine": None, "force": False, "timeout_s": None, "warnings": [],
        "ignored_options": []}


def opts(**kw):
    return CompatOptions(**{**BASE, **kw})


def doc3():
    return "# Title\n\n" + "".join(page_md(n, heading=f"Chapter {n}", body=f"Body {n}. ![](assets/p{n}_1.png)\n") for n in (1, 2, 3))


def asm(md, pages, o, **kw):
    from aidoc.compat.render import assemble_markdown
    return assemble_markdown(md, pages, o, doc_id=kw.get("doc_id", "d"), asset_base=kw.get("asset_base"),
                             assets_dir=kw.get("assets_dir"), cfg=kw.get("cfg"))


def _req(host=b"h:8765"):
    from starlette.requests import Request
    return Request({"type": "http", "scheme": "http", "headers": [(b"host", host)], "path": "/v1/convert/file",
                    "query_string": b"", "server": ("h", 8765), "client": ("1.2.3.4", 1)})


def test_default_mode_keeps_markers_and_placeholders_images():
    md, warns = asm(doc3(), 3, opts())
    assert md.count("<!-- page: ") == 3 and "<!-- image -->" in md and "assets/" not in md and warns == []


def test_placeholder_between_pages_not_before_first():
    md, _ = asm(doc3(), 3, opts(placeholder="\f"))
    parts = md.split("\f")
    assert len(parts) == 3 and not md.startswith("\f") and "<!-- page:" not in md
    assert "Title" in parts[0] and "Body 1" in parts[0] and "Body 3" in parts[2]


def test_missing_pages_become_empty_segments():
    md = "# T\n" + page_md(1) + page_md(3)                      # page 2 has no marker
    out, _ = asm(md, 3, opts(placeholder="\f"))
    parts = out.split("\f")
    assert len(parts) == 3 and parts[1].strip() == "" and "Page 3" in parts[2]


def test_trailing_pages_without_markers_still_get_segments():
    out, _ = asm(page_md(1) + page_md(2), 4, opts(placeholder="\f"))
    assert out.count("\f") == 3 and out.split("\f")[3] == ""


def test_placeholder_count_is_pages_minus_one_even_if_text_contains_it():
    md = page_md(1, body="code with \f inside") + page_md(2)
    out, _ = asm(md, 2, opts(placeholder="\f"))
    assert out.count("\f") == 2          # one from the text, one inserted


def test_keep_markers_with_placeholder_and_pageless_doc():
    out, _ = asm(doc3(), 3, opts(placeholder="\f", keep_page_markers=True))
    assert out.count("\f") == 2 and out.count("<!-- page: ") == 3
    assert out.split("\f")[1].startswith("<!-- page: 2 -->")
    out, _ = asm("# docx\n\nno pages", None, opts(placeholder="\f"))
    assert "\f" not in out and "no pages" in out


def test_referenced_and_embedded_images(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    for n in (1, 2, 3):
        (assets / f"p{n}_1.png").write_bytes(TINY_PNG)
    out, _ = asm(doc3(), 3, opts(image_mode="referenced"), doc_id="d1", asset_base="http://h:8765", assets_dir=assets)
    assert "](http://h:8765/v1/doc4ai/documents/d1/assets/p1_1.png)" in out

    class C:
        pass
    cfg = C()
    cfg.compat = C()
    cfg.compat.embed_images_max_mb = 20
    out, warns = asm(doc3(), 3, opts(image_mode="embedded"), doc_id="d1", assets_dir=assets, cfg=cfg)
    assert out.count("](data:image/png;base64,") == 3 and warns == []
    cfg.compat.embed_images_max_mb = 0
    out, warns = asm(doc3(), 3, opts(image_mode="embedded"), doc_id="d1", assets_dir=assets, cfg=cfg)
    assert "<!-- image -->" in out and any("embed" in w for w in warns)
    (assets / "p2_1.png").unlink()
    cfg.compat.embed_images_max_mb = 20
    out, warns = asm(doc3(), 3, opts(image_mode="embedded"), doc_id="d1", assets_dir=assets, cfg=cfg)
    assert out.count("](data:image/png;base64,") == 2 and any("p2_1.png" in w for w in warns)


def test_html_img_tags_are_handled_too():
    md = page_md(1, body='<img src="assets/p1_1.png" alt="x">\n')
    out, _ = asm(md, 1, opts())
    assert "assets/" not in out and "<!-- image -->" in out


def test_text_content_and_models():
    from aidoc.compat.render import ConvertDocumentResponse, ExportDocument, text_from_markdown
    txt = text_from_markdown("# H\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n**bold** <!-- page: 1 -->")
    assert "#" not in txt and "|" not in txt and "bold" in txt and "page:" not in txt
    r = ConvertDocumentResponse(document=ExportDocument(filename="x.pdf", md_content="m"), status="success", processing_time=1.0)
    d = r.model_dump()
    assert d["document"]["json_content"] is None and d["errors"] == [] and d["timings"] == {}


def test_text_content_keeps_the_placeholder_layout(make_doc, ctx):
    from aidoc.compat.render import build_response
    doc = make_doc("txtdoc", pages=3)
    r = build_response(ctx, _req(), doc, None, opts(placeholder="\f", to_formats=["md", "text"]), cached=False,
                       attached=False, started=0.0)
    assert r.document.text_content.count("\f") == 2 and "Chapter 2" in r.document.text_content.split("\f")[1]


def test_doc4ai_block_and_low_quality_status(make_doc, ctx):
    from aidoc.compat.render import build_response
    doc = make_doc("lowdoc", pages=2, status="low", quality={"score": 0.3, "level": "low", "reasons": ["chars_per_page"], "pages": []})
    r = build_response(ctx, _req(), doc, None, opts(), cached=False, attached=False, started=0.0)
    assert r.status == "partial_success" and r.errors[0].module_name == "doc4ai.quality"
    assert r.errors[0].category == "unknown" and r.errors[0].component_type == "pipeline"      # spike S2: docling enums
    assert "chars_per_page" in r.errors[0].error_message
    assert r.document.doc4ai["doc_id"] == doc["id"] and r.document.doc4ai["quality"]["level"] == "low"
    assert r.doc4ai == {"doc_id": doc["id"], "quality_level": "low", "cached": False}


def test_doc4ai_block_fields(make_doc, ctx):
    from aidoc.compat.render import build_response
    q = {"score": 0.93, "level": "warn", "reasons": [], "page_map": {"coverage": 1.0, "method": "mineru_render_plan"},
         "pages": [{"page": 2, "reasons": ["broken_text_layer"], "repaired_by": "mineru"}]}
    doc = make_doc("blockdoc", pages=3, status="warn", quality=q)
    o = opts(unsupported_formats=["json"], ignored_options=["pdf_backend"], warnings=["w1"])
    r = build_response(ctx, _req(), doc, None, o, cached=True, attached=False, started=0.0)
    b = r.document.doc4ai
    assert r.status == "success" and r.errors == []
    assert b["pages"] == 3 and b["page_map"] == {"coverage": 1.0, "method": "mineru_render_plan"}
    assert b["page_warnings"] == [{"page": 2, "reason": "broken_text_layer", "repaired": True}]
    assert b["cached"] is True and b["unsupported_formats"] == ["json"] and b["ignored_options"] == ["pdf_backend"]
    assert b["warnings"] == ["w1"] and b["untrusted_pages"] == [] and b["engine"] == "mineru"


def test_output_missing_is_a_failure_response(make_doc, ctx):
    import shutil
    from pathlib import Path

    from aidoc.compat.render import build_response
    doc = make_doc("gonedoc", pages=1)
    shutil.rmtree(Path(doc["output_dir"]))
    r = build_response(ctx, _req(), doc, None, opts(), cached=True, attached=False, started=0.0)
    assert r.status == "failure" and r.errors[0].error_message.startswith("output_missing")
