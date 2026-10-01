import json

from aidoc.mcp import docs as D
from tests.mcp.conftest import mcp_call, page_md


def _out(res):
    assert res.is_error is not True, res.content
    return res.structured_content


def test_outline_tracks_pages_and_skips_code_fences(ctx, make_doc):
    md = ("# Book\n\n<!-- page: 1 -->\n## Intro\n\n```\n# not a heading\n```\n<!-- page: 2 -->\n### Sub\ntext\n"
          "<!-- page: 3 -->\n## Next\n")
    doc = make_doc("o", pages=3, md=md)
    items, truncated = D.outline(D.load_doc(ctx, doc["id"]))
    assert [(i.level, i.title, i.page) for i in items] == [(1, "Book", None), (2, "Intro", 1), (3, "Sub", 2), (2, "Next", 3)]
    assert truncated is False
    many = "".join(page_md(n, heading=f"H{n}") for n in range(1, 260))
    doc2 = make_doc("many", pages=259, md=many)
    items, truncated = D.outline(D.load_doc(ctx, doc2["id"]))
    assert len(items) == 200 and truncated is True


def test_token_ranges_per_20_pages(ctx, make_doc):
    doc = make_doc("r", pages=45)
    est = D.token_ranges(D.load_doc(ctx, doc["id"]))
    assert [r.pages for r in est.ranges] == ["1-20", "21-40", "41-45"]
    assert est.total == sum(r.tokens for r in est.ranges) and est.per_page_avg == est.total // 45
    assert est.method in ("tiktoken", "bytes")
    flat = make_doc("flat", pages=0, md="just one blob of text without markers " * 50)
    est2 = D.token_ranges(D.load_doc(ctx, flat["id"]))
    assert [r.pages for r in est2.ranges] == ["all"] and est2.per_page_avg is None


def test_get_document_info_shape(mcp_server, mcp_env, make_doc):
    q = {"score": 0.91, "level": "warn", "reasons": ["pages_flagged"], "pages": [{"page": 2, "reasons": ["garbage"]}],
         "page_map": {"expected": 3, "found": 3, "coverage": 1.0, "alignment": {"ratio": 0.97}}}
    doc = make_doc("info", pages=3, quality=q, status="warn")
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "get_document_info", {"doc_id": doc["id"]}))
    assert out["doc_id"] == doc["id"] and out["title"] == "info" and out["source_name"] == "info.pdf" and out["pages"] == 3
    assert out["engine"] == "mineru" and out["lang"] == "cht"
    assert out["quality"] == {"level": "warn", "score": 0.91, "reasons": ["pages_flagged"]}
    assert out["page_map"] == {"expected": 3, "found": 3, "coverage": 1.0, "alignment": 0.97}
    assert out["flagged_pages"] == [{"page": 2, "reason": "garbage", "reasons": ["garbage"], "repaired": False}]
    assert [o["title"] for o in out["outline"]] == ["Title", "Chapter 1", "Chapter 2", "Chapter 3"]
    assert out["token_estimate"]["ranges"][0]["pages"] == "1-3" and out["token_estimate"]["total"] > 0
    assert out["chunks"] >= 1 and out["resources"][0] == f"doc4ai://documents/{doc['id']}"
    assert out["stale"] is False and out["job_id"] is None


def test_get_document_info_marks_stale_during_reconvert(mcp_server, mcp_env, make_doc):
    from aidoc.models import ConvertOptions
    doc = make_doc("busy", pages=1)
    job = mcp_env.ctx.store.create_job(ConvertOptions(output_dir=mcp_env.ctx.config.output_root()), "web")
    mcp_env.ctx.store.create_task(job, doc["source_path"], doc["sha256"], 1, 1.0, "cht", doc["output_dir"])
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "get_document_info", {"doc_id": doc["id"]}))
    assert out["stale"] is True and out["job_id"] == job


def test_output_missing_is_a_tool_error(mcp_server, mcp_env, make_doc):
    doc = make_doc("lost", pages=1)
    (D.load_doc(mcp_env.ctx, doc["id"]).md_path).unlink()
    raw, _ = mcp_env.issue()
    for tool, args in (("get_document_info", {"doc_id": doc["id"]}), ("read_document", {"doc_id": doc["id"]}),
                       ("get_chunks", {"doc_id": doc["id"]})):
        res = mcp_call(mcp_server, raw, tool, args)
        assert res.is_error is True, tool
        code = (res.structured_content or json.loads(res.content[0].text)).get("code")
        assert code == "output_missing", tool
    res = mcp_call(mcp_server, raw, "get_document_info", {"doc_id": "does-not-exist"})
    assert res.is_error and (res.structured_content or json.loads(res.content[0].text))["code"] == "document_not_found"


def test_get_document_info_fits_the_budget_for_a_badly_flagged_book(mcp_server, mcp_env, make_doc):
    """Review finding: 200 outline items + 200 flagged pages rendered to ~9,000 tokens (spec §1: ≤ 8,000)."""
    from aidoc.mcp.budget import estimate_tokens
    q = {"score": 0.4, "level": "low", "reasons": ["pages_flagged"],
         "pages": [{"page": n, "reasons": ["broken_text_layer", "garbage"], "repaired_by": "docling"} for n in range(1, 401)]}
    md = "".join(page_md(n, heading=f"Section {n}: a fairly long heading title for page {n}") for n in range(1, 401))
    doc = make_doc("bad", pages=400, md=md, quality=q, status="low")
    raw, _ = mcp_env.issue()
    res = mcp_call(mcp_server, raw, "get_document_info", {"doc_id": doc["id"]})
    out = _out(res)
    assert estimate_tokens(res.content[0].text)[0] <= 8000
    assert out["outline_truncated"] is True and out["flagged_pages_truncated"] is True
    assert out["outline"] and out["flagged_pages"] and out["token_estimate"]["total"] > 0
