import json

import pytest

from aidoc.mcp import docs as D
from aidoc.mcp.budget import estimate_tokens
from aidoc.mcp.errors import ToolFailure
from tests.mcp.conftest import mcp_call, page_md


def _out(res):
    assert res.is_error is not True, res.content
    return res.structured_content


def _err(res):
    assert res.is_error is True
    return res.structured_content or json.loads(res.content[0].text)


def test_parse_pages_and_heading_span(ctx, make_doc):
    assert D.parse_pages("12", 400) == (12, 12) and D.parse_pages("12-15", 400) == (12, 15) and D.parse_pages(" 3 - 4 ", 10) == (3, 4)
    for bad in ("0", "5-3", "401", "a-b", "", "1-2-3"):
        with pytest.raises(ToolFailure) as e:
            D.parse_pages(bad, 400)
        assert e.value.code == "page_range_invalid" and e.value.extra["pages"] == 400
    md = page_md(1, "Intro") + page_md(2, "Methods") + page_md(3) + page_md(4, "Results") + page_md(5)
    doc = make_doc("h", pages=5, md=md)
    v = D.load_doc(ctx, doc["id"])
    assert D.heading_span(v, "methods") == (2, 3, "Methods")          # until the next heading of the same level
    assert D.heading_span(v, "Results") == (4, 5, "Results")          # to the end
    with pytest.raises(ToolFailure) as e:
        D.heading_span(v, "Conclusions")
    assert e.value.code == "heading_not_found" and len(e.value.extra["closest"]) <= 3 and "Methods" in e.value.extra["closest"]


def test_read_pages_default_and_range(mcp_server, mcp_env, make_doc):
    doc = make_doc("r", pages=4, md="".join(page_md(n, body=f"![fig](assets/p{n}_1.png) body {n}") for n in range(1, 5)))
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "pages": "2-3"}))
    assert out["unit"] == "pages" and out["pages"] == {"start": 2, "end": 3} and out["truncated"] is False and out["next"] is None
    assert out["markdown"].startswith("<!-- page: 2 -->") and "<!-- page: 3 -->" in out["markdown"] and "<!-- page: 4 -->" not in out["markdown"]
    assert f"](doc4ai://documents/{doc['id']}/assets/p2_1.png)" in out["markdown"] and "](assets/" not in out["markdown"]
    assert out["tokens_est"] > 0 and out["title"] == "r" and out["stale"] is False
    whole = _out(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"]}))
    assert whole["pages"] == {"start": 1, "end": 4}


def test_read_truncates_at_page_boundary_with_next(mcp_server, mcp_env, make_doc):
    md = "".join(page_md(n, body=("word " * 400)) for n in range(1, 11))        # ~400 tokens per page
    doc = make_doc("big", pages=10, md=md)
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "max_tokens": 1000}))
    assert out["truncated"] is True and out["pages"]["start"] == 1 and 1 <= out["pages"]["end"] <= 3
    assert out["next"]["pages"] == f"{out['pages']['end'] + 1}-10" and out["next"]["offset"] is None
    assert estimate_tokens(out["markdown"])[0] <= 1000
    rest = _out(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "pages": out["next"]["pages"], "max_tokens": 8000}))
    assert rest["pages"]["end"] == 10 and rest["truncated"] is False


def test_single_oversized_page_is_sliced_not_dropped(mcp_server, mcp_env, make_doc):
    huge = "\n\n".join(f"row {i} | {'cell ' * 30} |" for i in range(600))      # one page, ~20k tokens
    doc = make_doc("table", pages=2, md=page_md(1, body=huge) + page_md(2, body="small"))
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "pages": "1-2", "max_tokens": 600}))
    assert out["markdown"].strip() and out["truncated"] is True and out["pages"] == {"start": 1, "end": 1}
    assert out["next"]["pages"] == "1-2" and out["next"]["offset"] > 0
    assert estimate_tokens(out["markdown"])[0] <= 600
    cont = _out(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "pages": "1-2", "offset": out["next"]["offset"], "max_tokens": 600}))
    assert cont["markdown"].strip() and cont["markdown"] not in out["markdown"]


def test_read_heading_mode_and_errors(mcp_server, mcp_env, make_doc):
    md = page_md(1, "Intro") + page_md(2, "Methods") + page_md(3) + page_md(4, "Results")
    doc = make_doc("h", pages=4, md=md)
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "heading": "methods"}))
    assert out["pages"] == {"start": 2, "end": 3}
    e = _err(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "heading": "Nope"}))
    assert e["code"] == "heading_not_found" and "closest" in e
    e = _err(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "pages": "9-12"}))
    assert e["code"] == "page_range_invalid" and e["pages"] == 4
    e = _err(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "pages": "1", "heading": "Intro"}))
    assert e["code"] == "invalid_arguments"


def test_read_pageless_document_uses_chunks(mcp_server, mcp_env, make_doc):
    md = "# Doc\n\n" + "\n\n".join(f"## Part {i}\n\n" + f"text {i} " * 120 for i in range(1, 9))
    doc = make_doc("flat", pages=0, md=md, source_name="flat.docx")
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "max_tokens": 700}))
    assert out["unit"] == "chunks" and out["pages"] is None and out["truncated"] is True
    assert out["next"]["chunk"] >= 1 and out["next"]["pages"] is None
    out2 = _out(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "chunk": out["next"]["chunk"], "max_tokens": 8000}))
    assert out2["truncated"] is False and "Part 8" in out2["markdown"]
    e = _err(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "pages": "1"}))
    assert e["code"] == "page_range_invalid"


def test_read_reports_page_warnings_and_stale(mcp_server, mcp_env, make_doc):
    from aidoc.models import ConvertOptions
    q = {"score": 0.9, "level": "warn", "reasons": ["pages_flagged"],
         "pages": [{"page": 2, "reasons": ["broken_text_layer"], "repaired_by": "docling"}, {"page": 4, "reasons": ["garbage"]}]}
    doc = make_doc("w", pages=4, quality=q, status="warn")
    job = mcp_env.ctx.store.create_job(ConvertOptions(output_dir=mcp_env.ctx.config.output_root()), "web")
    mcp_env.ctx.store.create_task(job, doc["source_path"], doc["sha256"], 1, 1.0, "cht", doc["output_dir"])
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "pages": "1-2"}))
    assert out["page_warnings"] == [{"page": 2, "reason": "broken_text_layer", "reasons": ["broken_text_layer"], "repaired": True}]
    assert out["stale"] is True and out["job_id"] == job


def test_budget_never_exceeds_server_cap(mcp_server, mcp_env, make_doc):
    doc = make_doc("cap", pages=30, md="".join(page_md(n, body="word " * 300) for n in range(1, 31)))
    mcp_env.ctx.config.mcp.response_token_budget = 1200
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "max_tokens": 8000}))
    mcp_env.ctx.config.mcp.response_token_budget = 8000
    assert estimate_tokens(out["markdown"])[0] <= 1200 and out["truncated"] is True


def test_text_block_is_markdown_under_a_header_not_json(mcp_server, mcp_env, make_doc):
    """Spec §5.1/§1: the text a model sees is the Markdown itself (plus one header line), within the budget — not
    the SDK's default indent-2 JSON copy of structuredContent."""
    doc = make_doc("t", pages=40, md="".join(page_md(n, body="word " * 300) for n in range(1, 41)))
    raw, _ = mcp_env.issue()
    res = mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "max_tokens": 8000})
    text = res.content[0].text
    assert text.startswith("<!-- doc4ai read_document: doc_id=") and "truncated=true" in text
    assert "\n<!-- page: 1 -->\n" in text and '"markdown"' not in text
    assert estimate_tokens(text)[0] <= 8000
    assert text.endswith(res.structured_content["markdown"])


def test_image_heavy_page_stays_within_budget_after_uri_rewrite(mcp_server, mcp_env, make_doc):
    """Review finding: tokens were counted before `](assets/x.png)` grew into `](doc4ai://documents/<id>/assets/x.png)`,
    so an 800-image page read with max_tokens=2000 came back at ~7,000 tokens."""
    figs = "\n\n".join(f"![f](assets/p1_{i}.png)" for i in range(800))
    doc = make_doc("figs", pages=2, md=page_md(1, body=figs) + page_md(2, body="tail"))
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "pages": "1-2", "max_tokens": 2000}))
    assert estimate_tokens(out["markdown"])[0] <= 2000 and out["truncated"] is True and out["next"]["offset"]
    assert "](assets/" not in out["markdown"] and "](doc4ai://" in out["markdown"]
    cont = _out(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "pages": "1-2",
                                                            "offset": out["next"]["offset"], "max_tokens": 2000}))
    assert cont["markdown"].lstrip().startswith("![f](doc4ai://") and estimate_tokens(cont["markdown"])[0] <= 2000


def test_offset_past_the_end_of_the_page_is_an_error_not_empty(mcp_server, mcp_env, make_doc):
    doc = make_doc("short", pages=2)
    raw, _ = mcp_env.issue()
    e = _err(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "pages": "1-2", "offset": 10 ** 6}))
    assert e["code"] == "invalid_arguments"


@pytest.mark.parametrize("body", [
    "a | b | c\n" * 4000,                                              # table-ish text: JSON adds little
    ('"q" ' + chr(92) * 2 + " \t\n") * 6000,                           # quotes, backslashes, tabs: JSON escaping inflates it
], ids=["table", "escapes"])
def test_whole_result_fits_the_budget_in_both_representations(mcp_server, mcp_env, make_doc, body):
    """Item 5: the model may be shown the text block OR structuredContent; each must fit max_tokens."""
    doc = make_doc("big", pages=1, md=page_md(1, body=body))
    raw, _ = mcp_env.issue()
    res = mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "max_tokens": 8000})
    text = "".join(b.text for b in res.content if getattr(b, "text", None))
    structured = json.dumps(res.structured_content, ensure_ascii=False, separators=(",", ":"))
    assert estimate_tokens(text)[0] <= 8000
    assert estimate_tokens(structured)[0] <= 8000
    assert res.structured_content["truncated"] is True and res.structured_content["markdown"]


def test_chunk_on_a_paged_document_is_refused_not_ignored(mcp_server, mcp_env, make_doc):
    doc = make_doc("paged", pages=3)
    raw, _ = mcp_env.issue()
    res = mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], "chunk": 2})
    assert res.is_error and res.structured_content["code"] == "invalid_arguments"
    assert "pages" in res.structured_content["hint"]
