"""Drive a Doc4AI Studio MCP server with the official SDK client the way Claude Code would: list tools, search,
read the document info, then read pages — and measure how many tokens each response puts in front of the model.

    uv run python tests/mcp/sdk_call.py --url http://box.tail0.ts.net:3333/mcp --token doc4ai_pat_... \
        --query "shoulder" --doc-id f52860b839234a56b0b2338b2585fa4f --pages 1-3 --pages 600-603 --pages 1190-1192

Exit code 1 when any response is over --assert-budget tokens or is a tool error."""
from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import asynccontextmanager

import anyio


@asynccontextmanager
async def _session(url: str, token: str, mode: str, client_name: str):
    """Per spike S3: `Client(streamable_http_client(url, http_client=httpx2.AsyncClient(headers=...)), mode=...)`."""
    import httpx2
    from mcp.client.client import Client
    from mcp.client.streamable_http import streamable_http_client
    from mcp.types import Implementation
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=httpx2.Timeout(30, read=300))
    async with http, Client(streamable_http_client(url, http_client=http), mode=mode,
                            client_info=Implementation(name=client_name, version="1.0")) as c:
        yield c


def _count(text: str) -> int:
    try:
        from aidoc.chunk import count_tokens
        return count_tokens(text)
    except Exception:  # noqa: BLE001  offline: bytes/3
        return len(text.encode("utf-8")) // 3


async def _run(url, token, mode, query, doc_id, pages, budget, client_name) -> dict:
    steps = []
    async with _session(url, token, mode, client_name) as s:
        tools = [t.name for t in (await s.list_tools()).tools]

        async def step(tool, args):
            t0 = time.perf_counter()
            res = await s.call_tool(tool, args)
            text = "".join(getattr(b, "text", "") or "" for b in res.content)
            sc = res.structured_content or {}
            summary = {k: sc.get(k) for k in ("truncated", "next", "pages", "total", "code") if k in sc}
            if tool == "search_library":
                summary["hits"] = [(h["title"], h["page"]) for h in sc.get("hits", [])][:5]
            structured = json.dumps(sc, ensure_ascii=False, separators=(",", ":")) if sc else ""
            steps.append({"tool": tool, "args": args, "tokens_text": _count(text), "tokens_est": sc.get("tokens_est"),
                          "tokens_structured": _count(structured),
                          "is_error": bool(res.is_error), "elapsed_ms": int((time.perf_counter() - t0) * 1000), "summary": summary})
            return sc

        if query:
            sc = await step("search_library", {"query": query})
            if doc_id is None and sc.get("hits"):
                doc_id = sc["hits"][0]["doc_id"]
        if doc_id:
            await step("get_document_info", {"doc_id": doc_id})
            for p in pages:
                await step("read_document", {"doc_id": doc_id, "pages": p})
        pv = s.protocol_version
    mx = max((st["tokens_text"] for st in steps), default=0)
    return {"protocol_version": pv, "tools": tools, "steps": steps, "max_tokens_text": mx,
            "ok": mx <= budget and not any(st["is_error"] for st in steps)}


def run(url, token, *, mode="auto", query=None, doc_id=None, pages=(), budget=8000, client_name="doc4ai-sdk-call") -> dict:
    return anyio.run(_run, url, token, mode, query, doc_id, tuple(pages), budget, client_name)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--mode", choices=("auto", "legacy"), default="auto")
    ap.add_argument("--query")
    ap.add_argument("--doc-id")
    ap.add_argument("--pages", action="append", default=[])
    ap.add_argument("--assert-budget", type=int, default=8000)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = run(a.url, a.token, mode=a.mode, query=a.query, doc_id=a.doc_id, pages=a.pages, budget=a.assert_budget)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"protocol {res['protocol_version']}; tools: {', '.join(res['tools'])}")
        for st in res["steps"]:
            flag = "ERROR" if st["is_error"] else ("OVER" if st["tokens_text"] > a.assert_budget else "ok")
            print(f"{flag:5} {st['tool']:18} {st['tokens_text']:6} tokens (structured {st['tokens_structured']:6})  "
                  f"{st['elapsed_ms']:5} ms  {json.dumps(st['summary'], ensure_ascii=False)[:120]}")
        print(f"max {res['max_tokens_text']} tokens; budget {a.assert_budget}; {'PASS' if res['ok'] else 'FAIL'}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
