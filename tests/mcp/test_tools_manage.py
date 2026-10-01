import json
import shutil

from tests.mcp.conftest import mcp_call


def _out(res):
    assert res.is_error is not True, res.content
    return res.structured_content


def _err(res):
    assert res.is_error is True, res.structured_content
    return res.structured_content or json.loads(res.content[0].text)


def test_cancel_job(mcp_server, mcp_env, fixtures):
    from aidoc.batch import register_source
    from aidoc.models import ConvertOptions
    ctx = mcp_env.ctx
    opts = ConvertOptions(output_dir=ctx.config.output_root())
    job = ctx.store.create_job(opts, "web")
    register_source(ctx.store, job, fixtures / "text.pdf", opts)
    ctx.store.refresh_job_status(job)
    raw, _ = mcp_env.issue(scopes=("doc4ai:read", "doc4ai:manage"))
    out = _out(mcp_call(mcp_server, raw, "cancel_job", {"job_id": job}))
    assert out == {"changed": True, "status": "cancelled"}
    assert ctx.store.list_tasks(job)[0]["status"] == "cancelled"
    out = _out(mcp_call(mcp_server, raw, "cancel_job", {"job_id": job}))
    assert out == {"changed": False, "status": "cancelled"}
    assert _err(mcp_call(mcp_server, raw, "cancel_job", {"job_id": "nope"}))["code"] == "job_not_found"


def test_reconvert_document(mcp_server, mcp_env, fixtures, tmp_root):
    from aidoc.batch import register_source
    from aidoc.models import ConvertOptions
    ctx = mcp_env.ctx
    src = tmp_root / "in.pdf"
    shutil.copy(fixtures / "text.pdf", src)
    opts = ConvertOptions(output_dir=ctx.config.output_root())
    job = ctx.store.create_job(opts, "web")
    register_source(ctx.store, job, src, opts)
    ctx.queue.process_next()
    doc = ctx.store.list_documents()[0]
    raw, _ = mcp_env.issue(scopes=("doc4ai:read", "doc4ai:manage"))
    out = _out(mcp_call(mcp_server, raw, "reconvert_document", {"doc_id": doc["id"], "engine": "docling"}))
    assert out["status"] == "queued" and out["job_id"] and out["doc_id"] is None
    j = ctx.store.get_job(out["job_id"])
    assert j["origin"] == "mcp" and j["options"]["engine"] == "docling"
    task = ctx.store.list_tasks(out["job_id"])[0]
    assert task["flags"] == {"force": True, "reconvert": True} and task["output_dir"] == doc["output_dir"]
    assert _err(mcp_call(mcp_server, raw, "reconvert_document", {"doc_id": doc["id"]}))["code"] == "already_converting"
    ctx.queue.process_next()
    src.unlink()
    for d in ctx.store.list_documents():
        ctx.store.update_document(d["id"], work_copy_path=None)
    assert _err(mcp_call(mcp_server, raw, "reconvert_document", {"doc_id": doc["id"]}))["code"] == "source_missing"
    assert _err(mcp_call(mcp_server, raw, "reconvert_document", {"doc_id": "nope"}))["code"] == "document_not_found"


def test_web_reconvert_endpoint_unchanged(client, ctx, fixtures, tmp_root):
    from aidoc.batch import register_source
    from aidoc.models import ConvertOptions
    src = tmp_root / "in2.pdf"
    shutil.copy(fixtures / "text.pdf", src)
    opts = ConvertOptions(output_dir=ctx.config.output_root())
    job = ctx.store.create_job(opts, "web")
    register_source(ctx.store, job, src, opts)
    ctx.queue.process_next()
    doc = ctx.store.list_documents()[0]
    r = client.post("/api/documents/reconvert", json={"ids": [doc["id"]]})
    assert r.status_code == 201 and r.json()["job"]["origin"] == "web"
    t = ctx.store.list_tasks(r.json()["job"]["id"])[0]
    assert t["flags"] == {"force": True, "auto_engine": True, "reconvert": True}
