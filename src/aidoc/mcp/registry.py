"""Tool registration with scope enforcement (MCP spec §2.2), `ToolFailure` → `isError` conversion (§5.1) and the
compact text rendering every result carries next to `structuredContent`.

Why a rendering of our own: for a structured tool the SDK's default text block is the output model dumped as
indent-2 JSON — a second, larger copy of the result (Markdown with every newline escaped). Spec §5.1 asks for
"structuredContent + 一段精簡文字", and §1 caps what the model sees at 8,000 tokens, so read results render as the
Markdown itself under a one-line header, chunks as Markdown, everything else as compact JSON (spike S6: a returned
`CallToolResult` passes through unchanged; S7: the client validates `structuredContent` only on success)."""
from __future__ import annotations

import functools
import inspect
import json

import anyio
from mcp.types import CallToolResult, ResourceLink, TextContent, ToolAnnotations
from pydantic import BaseModel

from aidoc.mcp.errors import ToolFailure
from aidoc.mcp.principal import current_principal
from aidoc.mcp.redact import redact_text, redact_value

TEXT_RESERVE_TOKENS = 100          # header/footer lines around budgeted Markdown (tools cut to budget minus this)


def _compact(d) -> str:
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"), default=str)


def failure_result(f: ToolFailure) -> CallToolResult:
    payload = redact_value(f.payload())
    text = f"{f.code}: {f.message}" + (f" ({f.hint})" if f.hint else "")
    if f.extra:
        text += " " + _compact(f.extra)
    text = redact_text(text)
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=payload, is_error=True)


def _read_header(d: dict) -> str:
    bits = [f"doc_id={d['doc_id']}", f"title={d['title']!r}"]
    if d.get("pages"):
        bits.append(f"pages {d['pages']['start']}-{d['pages']['end']}")
    else:
        bits.append(f"unit={d['unit']}")
    bits.append(f"truncated={str(d['truncated']).lower()}")
    if d.get("next"):
        bits.append("next=" + _compact({k: v for k, v in d["next"].items() if v is not None}))
    if d.get("page_warnings"):
        bits.append("page_warnings=" + _compact([{"page": w["page"], "reason": w["reason"]} for w in d["page_warnings"]]))
    if d.get("stale"):
        bits.append(f"stale=true job_id={d.get('job_id')}")
    return "<!-- doc4ai read_document: " + "; ".join(bits) + " -->\n"


def _chunks_text(d: dict) -> str:
    parts = []
    for c in d["chunks"]:
        where = f"pages {c['page_start']}-{c['page_end']}" if c.get("page_start") is not None else "no pages"
        parts.append(f"<!-- chunk {c['chunk_id']} · {' > '.join(c['heading_path'])} · {where} -->\n{c['text']}")
    tail = f"\n<!-- doc4ai get_chunks: total={d['total']}; next_cursor={d.get('next_cursor')} -->"
    return "\n\n".join(parts) + tail


def render_text(out: BaseModel) -> str:
    d = out.model_dump(mode="json")
    if "markdown" in d and "unit" in d:
        return _read_header(d) + d["markdown"]
    if "chunks" in d and "total" in d:
        return _chunks_text(d)
    return _compact(d)


def success_result(out) -> CallToolResult | object:
    if not isinstance(out, BaseModel):
        return out
    content: list = [TextContent(type="text", text=render_text(out))]
    for hit in getattr(out, "hits", None) or []:            # spec §5.3: every search hit as a resource_link
        content.append(ResourceLink(type="resource_link", uri=hit.uri, name=f"{hit.title} p.{hit.page}"
                                    if hit.page is not None else hit.title, mime_type="text/markdown"))
    return CallToolResult(content=content, structured_content=out.model_dump(mode="json"))


def doc4ai_tool(mcp, ctx, *, name: str, title: str, description: str, scope: str, read_only: bool = False,
                destructive: bool = False, idempotent: bool = False, open_world: bool = False):
    def deco(fn):
        is_async = inspect.iscoroutinefunction(fn)

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            principal = current_principal.get()
            try:
                if principal is None or not principal.has(scope):
                    raise ToolFailure("forbidden_scope", f"this token lacks the {scope} scope",
                                      hint="ask the Doc4AI Studio admin for a token with that scope")
                if name == "convert_path" and not ctx.config.mcp.local_path_roots:
                    raise ToolFailure("tool_disabled", "convert_path is disabled: no local_path_roots configured",
                                      hint="use convert_document (base64) instead")
                if is_async:
                    return success_result(await fn(*args, **kwargs))
                return success_result(await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs)))
            except ToolFailure as f:
                return failure_result(f)

        mcp.tool(name=name, title=title, description=description, structured_output=True,
                 annotations=ToolAnnotations(title=title, read_only_hint=read_only, destructive_hint=destructive,
                                             idempotent_hint=idempotent, open_world_hint=open_world))(wrapper)
        return fn
    return deco
