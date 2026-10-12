import base64
import json
import shutil
from pathlib import Path

import pymupdf

from aidoc.mcp import tools_verify as TV
from tests.mcp.conftest import mcp_call, page_md

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _out(res):
    assert res.is_error is not True, res.content
    return res.structured_content


def _err(res):
    assert res.is_error is True, res.structured_content
    sc = res.structured_content or json.loads(res.content[0].text)
    return sc.get("error", sc)


def _own(pdf):
    with pymupdf.open(pdf) as d:
        return "".join(f"<!-- page: {i} -->\n\n{p.get_text().strip()}\n\n" for i, p in enumerate(d, 1))


def _doc_with_pdf(ctx, make_doc, name="furn"):
    """A Library document whose work copy is paged_furniture.pdf and whose Markdown is its own text layer."""
    doc = make_doc(name, pages=8, md=_own(FIX / "paged_furniture.pdf"))
    wc = ctx.config.data_dir / "work" / name
    wc.mkdir(parents=True)
    shutil.copy(FIX / "paged_furniture.pdf", wc / "src.pdf")
    ctx.store.con.execute("UPDATE documents SET work_copy_path=? WHERE id=?", (str(wc), doc["id"]))
    return doc


def test_verify_document_requires_read_scope_and_returns_summary(mcp_server, mcp_env, make_doc):
    doc = _doc_with_pdf(mcp_env.ctx, make_doc)
    raw, _ = mcp_env.issue()
    sc = _out(mcp_call(mcp_server, raw, "verify_document", {"doc_id": doc["id"]}))
    assert sc["summary"]["level"] == "ok" and sc["page_map"]["coverage"] == 1.0 and sc["inferred"] is False
    assert sc["verifiable"] is True and sc["doc_id"] == doc["id"] and sc["pages"] == []
    noscope, _ = mcp_env.issue(scopes=("doc4ai:convert",))
    assert _err(mcp_call(mcp_server, noscope, "verify_document", {"doc_id": doc["id"]}))["code"] == "forbidden_scope"
    assert _err(mcp_call(mcp_server, raw, "verify_document", {"doc_id": "nope"}))["code"] == "document_not_found"


def test_verify_document_without_source_is_partial(mcp_server, mcp_env, make_doc):
    doc = make_doc("nosrc", pages=3)
    raw, _ = mcp_env.issue()
    sc = _out(mcp_call(mcp_server, raw, "verify_document", {"doc_id": doc["id"]}))
    assert sc["verifiable"] == "partial" and "partial" in sc["hint"]


def test_verify_markdown_against_library_pdf(mcp_server, mcp_env, make_doc):
    doc = _doc_with_pdf(mcp_env.ctx, make_doc, "furn2")
    raw, _ = mcp_env.issue(scopes=("doc4ai:read", "doc4ai:convert"))
    from aidoc.pagemap import MARKER
    md = base64.b64encode(MARKER.sub("", _own(FIX / "paged_furniture.pdf")).encode()).decode()
    sc = _out(mcp_call(mcp_server, raw, "verify_markdown", {"doc_id": doc["id"], "markdown_base64": md}))
    assert sc["inferred"] is True and sc["page_map"]["anchored"] >= 6 and sc["page_map"]["coverage"] is None
    mineru = base64.b64encode("第一頁\n\n---\n\n第二頁\n\n---\n\n第三頁".encode()).decode()
    sc2 = _out(mcp_call(mcp_server, raw, "verify_markdown", {"doc_id": doc["id"], "markdown_base64": mineru,
                                                              "format": "mineru"}))
    assert sc2["verifiable"] in (True, False, "partial")
    both = mcp_call(mcp_server, raw, "verify_markdown", {"doc_id": doc["id"], "pdf_base64": "AAAA", "markdown_base64": md})
    assert _err(both)["code"] == "invalid_arguments"
    neither = mcp_call(mcp_server, raw, "verify_markdown", {"markdown_base64": md})
    assert _err(neither)["code"] == "invalid_arguments"
    readonly, _ = mcp_env.issue()
    assert _err(mcp_call(mcp_server, readonly, "verify_markdown",
                         {"doc_id": doc["id"], "markdown_base64": md}))["code"] == "forbidden_scope"


def test_verify_markdown_with_uploaded_pdf_and_size_cap(mcp_server, mcp_env):
    raw, _ = mcp_env.issue(scopes=("doc4ai:convert",))
    md = base64.b64encode(_own(FIX / "text.pdf").encode()).decode()
    pdf = base64.b64encode((FIX / "text.pdf").read_bytes()).decode()
    sc = _out(mcp_call(mcp_server, raw, "verify_markdown", {"pdf_base64": pdf, "markdown_base64": md}))
    assert sc["doc_id"] is None and sc["summary"]["reasons"] == ["missing_table"]
    assert any("table_missing_on_page" in p["advisories"] for p in sc["pages"])
    bad = base64.b64encode(b"%PDF-1.4 not really").decode()
    assert _err(mcp_call(mcp_server, raw, "verify_markdown", {"pdf_base64": bad, "markdown_base64": md}))["code"] \
        == "unsupported_file"
    mcp_env.ctx.config.mcp.max_upload_mb = 0
    assert _err(mcp_call(mcp_server, raw, "verify_markdown", {"pdf_base64": pdf, "markdown_base64": md}))["code"] \
        == "file_too_large"


def test_budget_truncates_pages(ctx):
    pages = [{"page": p, "reasons": ["page_dropped"], "advisories": ["table_missing_on_page"]} for p in range(1, 1193)]
    j = {"verifiable": True, "page_map": {"method": "aidoc", "expected": 1192, "found": 1192, "coverage": 1.0},
         "summary": {"level": "warn", "score": 0.5, "reasons": [], "pages_flagged": 1192, "pages_unrepaired": 1192,
                     "advisories": 1192}, "pages": pages}
    out = TV.to_out(j, "d")
    assert len(out.pages) == 50 and out.pages_truncated is True
    from aidoc.mcp.budget import estimate_tokens
    from aidoc.mcp.registry import render_text
    small = TV.fit(out, 300)
    assert estimate_tokens(render_text(small))[0] <= 300 and small.pages_truncated is True
    assert estimate_tokens(render_text(TV.fit(TV.to_out(j, "d"), 8000)))[0] <= 8000


def test_verify_document_on_a_flagged_doc_lists_pages(mcp_server, mcp_env, make_doc):
    md = "".join(page_md(n) for n in range(1, 4)) + "<!-- page: 3 -->\n��� x\n"
    doc = make_doc("flag", pages=3, md=md.replace("Page 2 text", "P�2�text" * 30))
    raw, _ = mcp_env.issue()
    sc = _out(mcp_call(mcp_server, raw, "verify_document", {"doc_id": doc["id"]}))
    assert sc["verifiable"] == "partial" and isinstance(sc["pages"], list)
