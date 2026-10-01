"""Every tool failure — bad arguments, bad cursors, unknown tools, unexpected crashes — answers in the spec §5.1
shape: isError + structuredContent {code, message, hint}; never a raw pydantic dump or a stack trace."""
import logging

import pytest

from aidoc.mcp.budget import encode_cursor
from tests.mcp.conftest import mcp_call, raw_post


def _err(res) -> dict:
    assert res.is_error is True, res.structured_content
    sc = res.structured_content
    assert isinstance(sc, dict) and {"code", "message", "hint"} <= set(sc), res.content
    assert "Traceback" not in res.content[0].text and "pydantic" not in res.content[0].text.lower()
    return sc


@pytest.mark.parametrize("tool,args", [
    ("search_library", {"query": "x", "cursor": encode_cursor({"skip": "x"})}),
    ("search_library", {"query": "x", "cursor": encode_cursor({"skip": -3})}),
    ("list_documents", {"cursor": encode_cursor({"k": {}, "id": []})}),
])
def test_malformed_cursors_are_invalid_cursor(mcp_server, mcp_env, tool, args):
    raw, _ = mcp_env.issue()
    assert _err(mcp_call(mcp_server, raw, tool, args))["code"] == "invalid_cursor"


def test_chunk_cursor_types_and_foreign_cursors(mcp_server, mcp_env, make_doc):
    a, b = make_doc("one", pages=2), make_doc("two", pages=2)
    raw, _ = mcp_env.issue()
    for bad in ({"i": "z"}, {"i": -1}):
        assert _err(mcp_call(mcp_server, raw, "get_chunks", {"doc_id": a["id"], "cursor": encode_cursor(bad)}))["code"] == "invalid_cursor"
    first = mcp_call(mcp_server, raw, "list_documents", {"limit": 1}).structured_content
    assert first["next_cursor"]
    e = _err(mcp_call(mcp_server, raw, "list_documents", {"limit": 1, "sort": "title_asc", "cursor": first["next_cursor"]}))
    assert e["code"] == "invalid_cursor" and "sort" in e["message"]
    ok = mcp_call(mcp_server, raw, "list_documents", {"limit": 1, "cursor": first["next_cursor"]}).structured_content
    assert [d["doc_id"] for d in ok["documents"]] and ok["documents"][0]["doc_id"] != first["documents"][0]["doc_id"]
    assert b["id"] in {first["documents"][0]["doc_id"], ok["documents"][0]["doc_id"]}


@pytest.mark.parametrize("args,field", [({"max_tokens": 499}, "max_tokens"), ({"max_tokens": 8001}, "max_tokens"),
                                        ({"offset": -5}, "offset"), ({"pages": 7}, "pages")])
def test_argument_validation_is_invalid_arguments(mcp_server, mcp_env, make_doc, args, field):
    doc = make_doc("v", pages=2)
    raw, _ = mcp_env.issue()
    e = _err(mcp_call(mcp_server, raw, "read_document", {"doc_id": doc["id"], **args}))
    assert e["code"] == "invalid_arguments" and field in e["fields"] and field in e["hint"]
    row = mcp_env.ctx.store.list_mcp_calls(limit=1)[0]
    assert row["status"] == "tool_error" and row["error_code"] == "invalid_arguments"


def test_unknown_tool_lists_the_available_ones(mcp_server, mcp_env):
    raw, _ = mcp_env.issue()
    r = raw_post(mcp_server, raw, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nope", "arguments": {}}})
    res = r.json()["result"]
    assert res["isError"] is True and res["structuredContent"]["code"] == "unknown_tool"
    assert "read_document" in res["structuredContent"]["hint"] and "cancel_job" not in res["structuredContent"]["hint"]
    row = mcp_env.ctx.store.list_mcp_calls(limit=1)[0]
    assert row["tool_name"] == "nope" and row["error_code"] == "unknown_tool"


def test_unexpected_exception_is_internal_error_without_details(mcp_server, mcp_env, monkeypatch, caplog):
    from aidoc.mcp import docs as D

    def boom(*a, **k):
        raise RuntimeError("secret internals at C:/data/x")
    monkeypatch.setattr(D, "visible_docs", boom)
    caplog.set_level(logging.ERROR)
    raw, _ = mcp_env.issue()
    e = _err(mcp_call(mcp_server, raw, "list_documents", {}))
    assert e["code"] == "internal_error" and "secret internals" not in e["message"]
    assert any("secret internals" in (r.exc_text or "") + r.getMessage() or r.exc_info for r in caplog.records)
