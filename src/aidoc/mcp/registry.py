"""Tool registration with scope enforcement (MCP spec §2.2) and `ToolFailure` → `isError` conversion (§5.1)."""
from __future__ import annotations

import functools
import inspect

import anyio
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from aidoc.mcp.errors import ToolFailure
from aidoc.mcp.principal import current_principal


def failure_result(f: ToolFailure) -> CallToolResult:
    payload = f.payload()
    text = f"{f.code}: {f.message}" + (f" ({f.hint})" if f.hint else "")
    # per spike S6: the SDK passes a CallToolResult through unchanged. If it does not, replace the body of this
    # function with `raise ToolError(json.dumps(payload))` and keep the signature.
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=payload, is_error=True)


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
                    return await fn(*args, **kwargs)
                return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
            except ToolFailure as f:
                return failure_result(f)

        mcp.tool(name=name, title=title, description=description, structured_output=True,
                 annotations=ToolAnnotations(title=title, read_only_hint=read_only, destructive_hint=destructive,
                                             idempotent_hint=idempotent, open_world_hint=open_world))(wrapper)
        return fn
    return deco
