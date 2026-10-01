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


def test_search_with_nul_and_control_characters_is_not_a_crash(mcp_server, mcp_env, make_doc):
    """Review finding: a NUL in the query raised sqlite3.OperationalError (UnexpectedToolError)."""
    make_doc("n", pages=1, md=page_md(1, body="gradient descent here"))
    raw, _ = mcp_env.issue()
    for q in ("gradient\x00descent", "abc\x00", "\x07gradient"):
        res = mcp_call(mcp_server, raw, "search_library", {"query": q})
        assert res.is_error is not True, (q, res.content)
    assert _out(mcp_call(mcp_server, raw, "search_library", {"query": "gradient\x00descent"}))["hits"]


def test_search_store_error_is_a_tool_error(mcp_server, mcp_env, make_doc, monkeypatch):
    import sqlite3
    make_doc("n", pages=1, md=page_md(1, body="gradient descent here"))
    raw, _ = mcp_env.issue()

    def boom(*a, **kw):
        raise sqlite3.OperationalError("fts5: syntax error")
    monkeypatch.setattr(mcp_env.ctx.store, "search_hits", boom)              # the primitive search_library uses
    res = mcp_call(mcp_server, raw, "search_library", {"query": "gradient"})
    assert res.is_error is True and res.structured_content["code"] == "search_unavailable"


def _all_hits(base, raw, args):
    out, hits = _out(mcp_call(base, raw, "search_library", args)), []
    hits += out["hits"]
    while out.get("next_cursor"):
        out = _out(mcp_call(base, raw, "search_library", {**args, "cursor": out["next_cursor"]}))
        hits += out["hits"]
    return hits, out


def test_short_cjk_fallback_reaches_every_document(mcp_server, mcp_env, make_doc):
    """Item 6: one big document matching on 450 pages must not hide the others (the scan used to stop at 400 rows
    in insertion order)."""
    big = make_doc("big", pages=450, md="".join(page_md(n, body=f"身分驗證 第{n}頁") for n in range(1, 451)))
    small = [make_doc(f"s{i}", pages=1, md=page_md(1, body="另一份文件也提到驗證")) for i in range(3)]
    raw, _ = mcp_env.issue()
    hits, last = _all_hits(mcp_server, raw, {"query": "驗證", "limit": 2})
    assert {h["doc_id"] for h in hits} == {big["id"], *(d["id"] for d in small)}
    big_hits = [h for h in hits if h["doc_id"] == big["id"]]
    assert len(big_hits) == 3 and big_hits[0]["more_in_doc"] == 447
    assert last.get("truncated") is False


def test_search_says_when_it_hit_the_scan_cap(mcp_server, mcp_env, make_doc, monkeypatch):
    from aidoc import pageindex
    monkeypatch.setattr(pageindex, "SEARCH_SCAN_CAP", 50)
    make_doc("big", pages=120, md="".join(page_md(n, body=f"驗證 {n}") for n in range(1, 121)))
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "search_library", {"query": "驗證"}))
    assert out["truncated"] is True and "narrow" in out["hint"]
