import json

from tests.mcp.conftest import mcp_call, page_md


def _out(res):
    assert res.is_error is not True, res.content
    return res.structured_content


def test_get_chunks_pagination_and_cache_key(mcp_server, mcp_env, make_doc):
    md = "".join(page_md(n, heading=f"Sec {n}", body="\n\n".join([f"text {n} " * 30] * 5))
             for n in range(1, 13))                    # 5 paragraphs (~450 tokens) per section: 200 splits them
    doc = make_doc("c", pages=12, md=md)
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "get_chunks", {"doc_id": doc["id"], "limit": 5}))
    assert len(out["chunks"]) == 5 and out["total"] >= 12 and out["next_cursor"]
    c = out["chunks"][0]
    assert set(c) == {"chunk_id", "heading_path", "page_start", "page_end", "text"} and c["page_start"] == 1
    out2 = _out(mcp_call(mcp_server, raw, "get_chunks", {"doc_id": doc["id"], "limit": 5, "cursor": out["next_cursor"]}))
    assert out2["chunks"][0]["chunk_id"] != c["chunk_id"]
    small = _out(mcp_call(mcp_server, raw, "get_chunks", {"doc_id": doc["id"], "max_tokens": 200}))
    assert small["total"] > out["total"]                       # smaller chunks → more of them
    res = mcp_call(mcp_server, raw, "get_chunks", {"doc_id": doc["id"], "max_tokens": 50})
    assert res.is_error                                        # below the schema minimum


def test_get_chunks_respects_response_budget(mcp_server, mcp_env, make_doc):
    md = "".join(page_md(n, heading=f"S{n}", body="word " * 1500) for n in range(1, 6))
    doc = make_doc("cb", pages=5, md=md)
    mcp_env.ctx.config.mcp.response_token_budget = 2500
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "get_chunks", {"doc_id": doc["id"], "max_tokens": 2000, "limit": 20}))
    mcp_env.ctx.config.mcp.response_token_budget = 8000
    assert 1 <= len(out["chunks"]) <= 2 and out["next_cursor"]


def test_get_job_shape_and_not_found(mcp_server, mcp_env, make_doc, fixtures):
    from aidoc.batch import register_source
    from aidoc.models import ConvertOptions
    ctx = mcp_env.ctx
    opts = ConvertOptions(output_dir=ctx.config.output_root())
    job = ctx.store.create_job(opts, "web")
    tid, _ = register_source(ctx.store, job, fixtures / "text.pdf", opts)
    ctx.store.refresh_job_status(job)
    raw, _ = mcp_env.issue()
    out = _out(mcp_call(mcp_server, raw, "get_job", {"job_id": job}))
    assert out["job_id"] == job and out["status"] == "queued" and out["origin"] == "web"
    assert out["progress"] == {"pages_done": 0, "pages_total": 0} and out["error"] is None
    t = out["tasks"][0]
    assert t["task_id"] == tid and t["source_name"] == "text.pdf" and t["status"] == "queued" and t["doc_id"] is None
    ctx.queue.process_next()                                   # fake engine converts it
    out = _out(mcp_call(mcp_server, raw, "get_job", {"job_id": job}))
    assert out["status"] == "done" and out["tasks"][0]["doc_id"] and out["tasks"][0]["quality_level"] in ("ok", "warn", "low")
    res = mcp_call(mcp_server, raw, "get_job", {"job_id": "nope"})
    assert res.is_error and (res.structured_content or json.loads(res.content[0].text))["code"] == "job_not_found"
