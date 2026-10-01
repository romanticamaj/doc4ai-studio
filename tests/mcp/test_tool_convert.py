import base64
import json

import pytest

from aidoc.mcp import tools_convert as TC
from aidoc.mcp.errors import ToolFailure
from tests.mcp.conftest import mcp_call


def _out(res):
    assert res.is_error is not True, res.content
    return res.structured_content


def _err(res):
    assert res.is_error is True, res.structured_content
    return res.structured_content or json.loads(res.content[0].text)


def _b64(path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


@pytest.mark.parametrize("wrap", [
    lambda s: s, lambda s: "data:application/pdf;base64," + s, lambda s: "\n".join(s[i:i + 76] for i in range(0, len(s), 76)),
    lambda s: s.replace("+", "-").replace("/", "_"), lambda s: " " + s + "\n",
])
def test_base64_variants(fixtures, wrap):
    raw = (fixtures / "text.pdf").read_bytes()
    assert TC.decode_content(wrap(base64.b64encode(raw).decode()), 10 ** 9) == raw


def test_decode_rejects_garbage_and_oversize():
    with pytest.raises(ToolFailure) as e:
        TC.decode_content("@@@ not base64 @@@", 10 ** 9)
    assert e.value.code == "invalid_base64"
    with pytest.raises(ToolFailure) as e:
        TC.decode_content("A" * 4000, limit_bytes=1000)             # 3000 bytes declared by length: refused before decoding
    assert e.value.code == "file_too_large" and e.value.extra["limit_bytes"] == 1000


def test_check_magic(fixtures):
    TC.check_magic("x.pdf", (fixtures / "text.pdf").read_bytes())
    TC.check_magic("x.docx", b"PK\x03\x04rest")
    TC.check_magic("notes.md", b"# plain text")
    for name, data in (("x.pdf", b"PK\x03\x04zip"), ("x.png", b"%PDF-1.4"), ("x.exe", b"MZ"), ("x.md", b"%PDF-1.4")):
        with pytest.raises(ToolFailure) as e:
            TC.check_magic(name, data)
        assert e.value.code == "unsupported_file", name


def test_convert_document_creates_mcp_job_and_get_job_sees_it(mcp_server, mcp_env, fixtures):
    raw, tid = mcp_env.issue(scopes=("doc4ai:read", "doc4ai:convert"))
    out = _out(mcp_call(mcp_server, raw, "convert_document", {"filename": "text.pdf", "content_base64": _b64(fixtures / "text.pdf")}))
    assert out["job_id"] and out["status"] == "queued" and out["doc_id"] is None
    job = mcp_env.ctx.store.get_job(out["job_id"])
    assert job["origin"] == "mcp"
    row = mcp_env.ctx.store.list_mcp_calls(limit=1)[0]
    assert row["job_id"] == out["job_id"] and row["tool_name"] == "convert_document" and row["token_id"] == tid
    summary = json.loads(row["args_summary"])
    assert summary["content_base64"]["len"] > 0 and "JVBER" not in row["args_summary"]        # base64 never logged
    assert mcp_env.ctx.store.active_mcp_jobs(tid) == 1
    mcp_env.ctx.queue.process_next()
    done = _out(mcp_call(mcp_server, raw, "get_job", {"job_id": out["job_id"]}))
    assert done["status"] == "done" and done["tasks"][0]["doc_id"]
    assert mcp_env.ctx.store.active_mcp_jobs(tid) == 0
    again = _out(mcp_call(mcp_server, raw, "convert_document", {"filename": "text.pdf", "content_base64": _b64(fixtures / "text.pdf")}))
    assert again["status"] == "cached" and again["doc_id"] == done["tasks"][0]["doc_id"] and again["job_id"] is None
    forced = _out(mcp_call(mcp_server, raw, "convert_document", {"filename": "text.pdf", "content_base64": _b64(fixtures / "text.pdf"), "force": True}))
    assert forced["status"] == "queued" and forced["job_id"]


def test_convert_document_limits(mcp_server, mcp_env, fixtures):
    ctx = mcp_env.ctx
    raw, _tid = mcp_env.issue(scopes=("doc4ai:read", "doc4ai:convert"))
    e = _err(mcp_call(mcp_server, raw, "convert_document", {"filename": "evil.exe", "content_base64": "TVo="}))
    assert e["code"] == "unsupported_file"
    ctx.config.mcp.max_upload_mb = 1
    e = _err(mcp_call(mcp_server, raw, "convert_document", {"filename": "a.txt", "content_base64": "A" * (1_500_000)}))
    assert e["code"] == "file_too_large" and e["limit_bytes"] == 1024 * 1024
    ctx.config.mcp.max_upload_mb = 20
    ctx.config.mcp.max_concurrent_jobs_per_token = 1
    first = _out(mcp_call(mcp_server, raw, "convert_document", {"filename": "text.pdf", "content_base64": _b64(fixtures / "text.pdf")}))
    e = _err(mcp_call(mcp_server, raw, "convert_document", {"filename": "sample.docx", "content_base64": _b64(fixtures / "sample.docx")}))
    assert e["code"] == "too_many_jobs" and e["limit"] == 1 and first["job_id"]
    ctx.config.mcp.max_concurrent_jobs_per_token = 3
    ctx.config.limits.disk_space_factor = 10 ** 12                       # no disk is that big (the file is ~80 KB)
    e = _err(mcp_call(mcp_server, raw, "convert_document", {"filename": "sample.docx", "content_base64": _b64(fixtures / "sample.docx")}))
    assert e["code"] == "insufficient_disk" and e["needed"] > e["free"]
    ctx.config.limits.disk_space_factor = 3


def test_convert_document_wait_and_progress(mcp_server, mcp_env, fixtures):
    raw, _ = mcp_env.issue(scopes=("doc4ai:read", "doc4ai:convert"))
    seen = []

    async def progress(p, total, message):
        seen.append((p, total, message))
    import threading
    threading.Timer(0.5, mcp_env.ctx.queue.process_next).start()          # the worker is off in tests: run one task
    out = _out(mcp_call(mcp_server, raw, "convert_document", {"filename": "text.pdf", "content_base64": _b64(fixtures / "text.pdf"),
                                                              "wait_seconds": 20}, progress=progress))
    assert out["status"] == "done" and out["doc_id"]
    assert seen and seen[-1][0] >= seen[0][0]                               # monotone progress (per spike S8, modern path)


def test_convert_scope_required(mcp_server, mcp_env):
    raw, _ = mcp_env.issue()
    assert _err(mcp_call(mcp_server, raw, "convert_document", {"filename": "a.txt", "content_base64": "aGk="}))["code"] == "forbidden_scope"


def test_check_magic_webp_needs_the_webp_fourcc():
    TC.check_magic("x.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    with pytest.raises(ToolFailure):
        TC.check_magic("x.webp", b"RIFF\x00\x00\x00\x00WAVEfmt ")     # a WAV file is RIFF too (index A-M10)
