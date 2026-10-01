"""Per-token cap on conversions in flight (S-phase finding 4): atomic, and every MCP-originated job counts."""
import base64
import shutil
import threading

from aidoc.models import ConvertOptions
from tests.mcp.conftest import mcp_call


def test_store_reserves_jobs_atomically(ctx):
    opts = ConvertOptions(output_dir=ctx.config.output_root())
    got, barrier = [], threading.Barrier(12)

    def go():
        barrier.wait()
        got.append(ctx.store.create_mcp_job(opts, token_id="t1", limit=3))
    threads = [threading.Thread(target=go) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([g for g in got if g]) == 3 and got.count(None) == 9
    assert ctx.store.active_mcp_jobs("t1") == 3 and ctx.store.active_mcp_jobs("t2") == 0
    assert ctx.store.create_mcp_job(opts, token_id="t2", limit=3)                  # per token
    job = next(g for g in got if g)
    ctx.store.set_job_status(job, "done")                                          # finished jobs free a slot
    assert ctx.store.active_mcp_jobs("t1") == 2


def test_concurrent_convert_calls_respect_the_cap(mcp_server, mcp_env):
    ctx = mcp_env.ctx
    ctx.config.mcp.max_concurrent_jobs_per_token = 2
    raw, tid = mcp_env.issue(scopes=("doc4ai:read", "doc4ai:convert"))
    results, barrier = [], threading.Barrier(6)

    def go(i):
        content = base64.b64encode(f"# note {i}\n\nunique body {i}\n".encode()).decode()
        barrier.wait()
        results.append(mcp_call(mcp_server, raw, "convert_document", {"filename": f"note{i}.md", "content_base64": content}))
    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ok = [r for r in results if not r.is_error]
    refused = [r for r in results if r.is_error]
    assert len(ok) == 2 and len(refused) == 4
    assert all(r.structured_content["code"] == "too_many_jobs" for r in refused)
    assert len([j for j in ctx.store.list_jobs() if j["origin"] == "mcp"]) == 2
    assert ctx.store.active_mcp_jobs(tid) == 2


def test_reconvert_jobs_count_toward_the_cap(mcp_server, mcp_env, fixtures, tmp_root):
    from aidoc.batch import register_source
    ctx = mcp_env.ctx
    src = tmp_root / "in.pdf"
    shutil.copy(fixtures / "text.pdf", src)
    opts = ConvertOptions(output_dir=ctx.config.output_root())
    register_source(ctx.store, ctx.store.create_job(opts, "web"), src, opts)
    ctx.queue.process_next()
    doc = ctx.store.list_documents()[0]
    ctx.config.mcp.max_concurrent_jobs_per_token = 1
    raw, tid = mcp_env.issue(scopes=("doc4ai:read", "doc4ai:convert", "doc4ai:manage"))
    out = mcp_call(mcp_server, raw, "reconvert_document", {"doc_id": doc["id"]})
    assert not out.is_error and ctx.store.active_mcp_jobs(tid) == 1
    content = base64.b64encode(b"# other\n\nbody\n").decode()
    refused = mcp_call(mcp_server, raw, "convert_document", {"filename": "other.md", "content_base64": content})
    assert refused.is_error and refused.structured_content["code"] == "too_many_jobs"
    # and the other way round: a convert job in flight blocks a reconvert
    ctx.queue.cancel_job(out.structured_content["job_id"])
    assert ctx.store.active_mcp_jobs(tid) == 0
    assert not mcp_call(mcp_server, raw, "convert_document", {"filename": "other.md", "content_base64": content}).is_error
    again = mcp_call(mcp_server, raw, "reconvert_document", {"doc_id": doc["id"]})
    assert again.is_error and again.structured_content["code"] == "too_many_jobs"
