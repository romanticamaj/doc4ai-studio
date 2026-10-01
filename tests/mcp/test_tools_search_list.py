import json

from aidoc.mcp.budget import encode_cursor
from tests.mcp.conftest import mcp_call, page_md


def _out(res):
    assert res.is_error is not True, res.content
    return res.structured_content


def test_search_hits_grouped_per_document(mcp_server, mcp_env, make_doc):
    md = "".join(page_md(n, body=f"gradient descent appears on page {n}. " * 3) for n in range(1, 7))
    a = make_doc("alpha", pages=6, md=md)
    b = make_doc("beta", pages=1, md=page_md(1, body="gradient descent once"))
    make_doc("gamma", pages=1, md=page_md(1, body="nothing relevant"))
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "search_library", {"query": "gradient descent"}))
    by_doc = {}
    for h in out["hits"]:
        by_doc.setdefault(h["doc_id"], []).append(h)
    assert len(by_doc[a["id"]]) == 3 and by_doc[a["id"]][0]["more_in_doc"] == 3       # 6 pages matched, 3 shown
    assert len(by_doc[b["id"]]) == 1 and by_doc[b["id"]][0]["more_in_doc"] == 0
    hit = by_doc[b["id"]][0]
    assert hit["title"] == "beta" and hit["page"] == 1 and "gradient" in hit["snippet"] and hit["score"] > 0
    assert hit["uri"] == f"doc4ai://documents/{b['id']}/pages/1"
    assert out["query"] == "gradient descent" and out["next_cursor"] is None


def test_search_filters_limit_and_cursor(mcp_server, mcp_env, make_doc):
    docs = [make_doc(f"d{i}", pages=1, md=page_md(1, body=f"needle number {i}"), engine="docling" if i % 2 else "mineru",
                     status="warn" if i == 0 else "ok") for i in range(5)]
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "search_library", {"query": "needle", "limit": 2}))
    assert len(out["hits"]) == 2 and out["next_cursor"]
    out2 = _out(mcp_call(mcp_server, raw, "search_library", {"query": "needle", "limit": 2, "cursor": out["next_cursor"]}))
    assert len(out2["hits"]) == 2 and {h["doc_id"] for h in out2["hits"]}.isdisjoint({h["doc_id"] for h in out["hits"]})
    out3 = _out(mcp_call(mcp_server, raw, "search_library", {"query": "needle", "filters": {"engine": "docling"}}))
    assert {h["doc_id"] for h in out3["hits"]} == {docs[1]["id"], docs[3]["id"]}
    out4 = _out(mcp_call(mcp_server, raw, "search_library", {"query": "needle", "filters": {"level": "warn"}}))
    assert {h["doc_id"] for h in out4["hits"]} == {docs[0]["id"]}
    out5 = _out(mcp_call(mcp_server, raw, "search_library", {"query": "needle", "filters": {"doc_ids": [docs[2]["id"]]}}))
    assert [h["doc_id"] for h in out5["hits"]] == [docs[2]["id"]]


def test_search_errors(mcp_server, mcp_env, make_doc):
    make_doc("d", pages=1)
    raw, _ = mcp_env.issue()
    res = mcp_call(mcp_server, raw, "search_library", {"query": "x", "cursor": "!!!"})
    assert res.is_error and (res.structured_content or json.loads(res.content[0].text)).get("code", "invalid_cursor") == "invalid_cursor"
    assert _out(mcp_call(mcp_server, raw, "search_library", {"query": "學"}))["hits"] == []        # 1 char: no crash
    res = mcp_call(mcp_server, raw, "search_library", {"query": "", "limit": 50})
    assert res.is_error                                                                             # schema validation


def test_list_documents_sort_filter_cursor(mcp_server, mcp_env, make_doc):
    import time
    names = ["zeta", "alpha", "mid"]
    for i, n in enumerate(names):
        make_doc(n, pages=i + 1, engine="docling" if n == "mid" else "mineru",
                 quality={"score": 0.9, "level": "ok", "reasons": [], "pages": [{"page": 1, "reasons": ["garbage"]}] if n == "zeta" else []})
        time.sleep(0.01)
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "list_documents", {}))
    assert [d["title"] for d in out["documents"]] == ["mid", "alpha", "zeta"] and out["total"] == 3   # updated_desc
    d = out["documents"][2]
    assert d["source_name"] == "zeta.pdf" and d["pages"] == 1 and d["engine"] == "mineru" and d["flagged_pages"] == 1
    assert d["quality"] == {"level": "ok", "score": 0.9} and d["updated_at"] > 0
    out = _out(mcp_call(mcp_server, raw, "list_documents", {"sort": "title_asc", "limit": 2}))
    assert [d["title"] for d in out["documents"]] == ["alpha", "mid"] and out["next_cursor"]
    out2 = _out(mcp_call(mcp_server, raw, "list_documents", {"sort": "title_asc", "limit": 2, "cursor": out["next_cursor"]}))
    assert [d["title"] for d in out2["documents"]] == ["zeta"] and out2["next_cursor"] is None
    assert [d["title"] for d in _out(mcp_call(mcp_server, raw, "list_documents", {"filters": {"engine": "docling"}}))["documents"]] == ["mid"]
    assert [d["title"] for d in _out(mcp_call(mcp_server, raw, "list_documents", {"filters": {"flagged": True}}))["documents"]] == ["zeta"]
    assert [d["title"] for d in _out(mcp_call(mcp_server, raw, "list_documents", {"filters": {"q": "ALP"}}))["documents"]] == ["alpha"]
    res = mcp_call(mcp_server, raw, "list_documents", {"cursor": encode_cursor({"bogus": 1})})
    assert res.is_error


def test_orphaned_documents_are_not_listed_or_searched(mcp_server, mcp_env, make_doc):
    make_doc("gone", pages=1, md=page_md(1, body="unique needle"), status="orphaned")
    raw, _ = mcp_env.issue()
    assert _out(mcp_call(mcp_server, raw, "list_documents", {}))["documents"] == []
    assert _out(mcp_call(mcp_server, raw, "search_library", {"query": "unique needle"}))["hits"] == []
