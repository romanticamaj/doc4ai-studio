import json
import subprocess
import sys

from tests.mcp.conftest import page_md


def test_sdk_call_script_runs_search_info_read(mcp_server, mcp_env, make_doc):
    doc = make_doc("book", pages=30, md="".join(page_md(n, heading=f"Chapter {n}", body="orthopaedic text " * 40) for n in range(1, 31)))
    raw, _ = mcp_env.issue()
    from tests.mcp import sdk_call
    res = sdk_call.run(f"{mcp_server}/mcp", raw, query="orthopaedic", doc_id=doc["id"], pages=("1-3", "10-12", "28-30"), budget=8000)
    assert res["protocol_version"] == "2026-07-28" and "read_document" in res["tools"]
    assert [s["tool"] for s in res["steps"]] == ["search_library", "get_document_info", "read_document", "read_document", "read_document"]
    assert res["ok"] is True and res["max_tokens_text"] <= 8000
    assert all(s["tokens_text"] > 0 and s["elapsed_ms"] >= 0 for s in res["steps"])
    legacy = sdk_call.run(f"{mcp_server}/mcp", raw, mode="legacy", query="orthopaedic", doc_id=doc["id"], pages=("1-2",))
    assert legacy["protocol_version"] == "2025-11-25" and legacy["ok"]


def test_sdk_call_cli_exit_codes(mcp_server, mcp_env, make_doc):
    doc = make_doc("book", pages=3)
    raw, _ = mcp_env.issue()
    cmd = [sys.executable, "tests/mcp/sdk_call.py", "--url", f"{mcp_server}/mcp", "--token", raw, "--query", "Chapter",
           "--doc-id", doc["id"], "--pages", "1-3", "--json"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["ok"] is True and len(out["steps"]) == 3
    p = subprocess.run(cmd + ["--assert-budget", "5"], capture_output=True, text=True, timeout=120, check=False)
    assert p.returncode == 1
