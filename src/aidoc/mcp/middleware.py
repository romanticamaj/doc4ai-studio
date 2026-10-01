"""SDK middleware: principal ContextVar for the handler, tools/list scope filter (D3), one log row per message.

Spike findings this module depends on (docs/superpowers/spikes/2026-10-mcp-sdk.md):
* S4 — `rctx.request` is the Starlette Request in both eras; the gate's `CallState` sits in `scope["state"]`.
  In the 2026-07-28 era a `tools/call` with arguments makes the SDK run an *internal* `tools/list` through this
  chain (same request); it is filtered like any listing but never logged (`rctx.method != state.method`).
* S5 — client identity: `rctx.session.client_params.client_info` (modern envelope); on a legacy `initialize`
  it is only in `rctx.params["clientInfo"]`, and later legacy messages carry none (recalled per token + UA).
* `call_next` returns the wire-form result (a dict, camelCase keys); models are handled too."""
from __future__ import annotations

import json
import time

from mcp.shared.exceptions import MCPError

from aidoc.mcp.budget import estimate_tokens
from aidoc.mcp.principal import (
    SCOPE_CONVERT,
    SCOPE_CONVERT_LOCAL,
    SCOPE_MANAGE,
    SCOPE_READ,
    Principal,
    current_principal,
)

TOOL_SCOPES: dict[str, str] = {
    "search_library": SCOPE_READ, "list_documents": SCOPE_READ, "get_document_info": SCOPE_READ,
    "read_document": SCOPE_READ, "get_chunks": SCOPE_READ, "get_job": SCOPE_READ,
    "convert_document": SCOPE_CONVERT, "convert_path": SCOPE_CONVERT_LOCAL,
    "cancel_job": SCOPE_MANAGE, "reconvert_document": SCOPE_MANAGE,
    # Phase 1.5: "delete_document": SCOPE_MANAGE
}


def visible_tools(names: list[str], principal: Principal | None, cfg) -> list[str]:
    if principal is None:
        return []
    out = []
    for n in names:
        scope = TOOL_SCOPES.get(n)
        if scope is None or not principal.has(scope):
            continue
        if n == "convert_path" and not cfg.mcp.local_path_roots:
            continue
        out.append(n)
    return out


def _get(result, key: str, attr: str | None = None):
    """A field of a result that may be the wire dict (camelCase) or a pydantic model (snake_case)."""
    if isinstance(result, dict):
        return result.get(key)
    return getattr(result, attr or key, None)


def _error_payload(result) -> dict | None:
    sc = _get(result, "structuredContent", "structured_content")
    if isinstance(sc, dict):
        inner = sc.get("error", sc)
        return inner if isinstance(inner, dict) else sc
    for block in _get(result, "content") or []:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if text:
            try:
                d = json.loads(text)
                return d if isinstance(d, dict) else None
            except ValueError:
                return None
    return None


def classify_tool_error(result) -> tuple[str, str | None]:
    code = (_error_payload(result) or {}).get("code")
    return ("forbidden_scope" if code == "forbidden_scope" else "tool_error", code)


def _filter_listing(result, allowed: set[str]):
    """Keep the tools this principal may call, sorted by name (a stable listing caches well client-side)."""
    if isinstance(result, dict):
        result["tools"] = sorted((t for t in result.get("tools") or [] if t.get("name") in allowed),
                                 key=lambda t: t["name"])
        return result
    result.tools = sorted((t for t in result.tools if t.name in allowed), key=lambda t: t.name)
    return result


def _tool_names(result) -> list[str]:
    tools = _get(result, "tools") or []
    return [t.get("name") if isinstance(t, dict) else t.name for t in tools]


def _call_state(rctx):
    req = getattr(rctx, "request", None)
    if req is None:
        return None
    return (req.scope.get("state") or {}).get("doc4ai")


def _client_info(rctx) -> tuple[str | None, str | None]:
    try:
        info = rctx.session.client_params.client_info
    except AttributeError:
        info = None
    if info is not None:
        return info.name, info.version
    if rctx.method == "initialize":
        ci = (rctx.params or {}).get("clientInfo")
        if isinstance(ci, dict) and isinstance(ci.get("name"), str):
            return ci["name"], ci.get("version") if isinstance(ci.get("version"), str) else None
    return None, None


def _protocol_version(rctx) -> str | None:
    if rctx.method == "initialize":                 # S5: ctx.protocol_version is the pre-handshake default here
        pv = (rctx.params or {}).get("protocolVersion")
        if isinstance(pv, str):
            return pv
    return rctx.protocol_version


def _response_size(result) -> tuple[int | None, int | None]:
    if result is None:
        return None, None
    try:
        raw = (json.dumps(result, ensure_ascii=False, default=str) if isinstance(result, dict)
               else result.model_dump_json(by_alias=True, exclude_none=True))
    except Exception:  # noqa: BLE001
        return None, None
    blocks = _get(result, "content") or []
    text = "".join((b.get("text") if isinstance(b, dict) else getattr(b, "text", None)) or "" for b in blocks)
    if not text and not blocks:
        text = raw
    tokens = estimate_tokens(text)[0] if len(text) < 400_000 else len(text.encode("utf-8")) // 3
    return len(raw.encode("utf-8")), tokens


def make_middleware(ctx, recorder):
    async def doc4ai_middleware(rctx, call_next):
        state = _call_state(rctx)
        token = current_principal.set(state.principal if state else None)
        status, error_code, result = "ok", None, None
        t0 = time.perf_counter()
        try:
            result = await call_next(rctx)
            if rctx.method == "tools/list" and state is not None and result is not None:
                allowed = set(visible_tools(_tool_names(result), state.principal, ctx.config))
                result = _filter_listing(result, allowed)
            if rctx.method == "tools/call" and _get(result, "isError", "is_error"):
                status, error_code = classify_tool_error(result)
            return result
        except MCPError as e:
            data = getattr(e.error, "data", None)
            if isinstance(data, dict) and data.get("code") == "forbidden_scope":
                status, error_code = "forbidden_scope", "forbidden_scope"
            else:
                status, error_code = "protocol_error", str(getattr(e.error, "code", "mcp_error"))
            raise
        except Exception as e:
            status, error_code = "protocol_error", type(e).__name__
            raise
        finally:
            current_principal.reset(token)
            internal = state is not None and state.method is not None and rctx.method != state.method
            if state is not None and rctx.request_id is not None and not internal:
                _record(ctx, recorder, rctx, state, status, error_code, result, t0)
    return doc4ai_middleware


def _record(ctx, recorder, rctx, state, status, error_code, result, t0) -> None:
    params = rctx.params or {}
    token_id = state.principal.token_id
    name, version = _client_info(rctx)
    if name and rctx.method == "initialize":
        recorder.remember_identity(token_id, state.user_agent, name, version)
    elif not name:
        name, version = recorder.recall_identity(token_id, state.user_agent)
    pv = _protocol_version(rctx)
    client_id = recorder.note_client(token_id=token_id, client_name=name, client_version=version,
                                     protocol_version=pv, user_agent=state.user_agent, ip=state.ip)
    size, tokens = _response_size(result)
    sc = _get(result, "structuredContent", "structured_content") if result is not None else None
    tool = params.get("name") if rctx.method == "tools/call" else None
    job_id = sc.get("job_id") if isinstance(sc, dict) and tool in ("convert_document", "convert_path") else None
    recorder.record(status=status, token_id=token_id, client_id=client_id, method=rctx.method, tool_name=tool,
                    resource_uri=params.get("uri") if rctx.method == "resources/read" else None,
                    args=params.get("arguments") if rctx.method == "tools/call" else None, error_code=error_code,
                    http_status=200, duration_ms=int((time.perf_counter() - t0) * 1000), response_bytes=size,
                    response_tokens_est=tokens, ip=state.ip, protocol_version=pv, job_id=job_id, ts=state.ts)
    state.logged = True
    label = (f"{name}/{version}" if version else name) if name else state.user_agent
    recorder.touch_token(token_id, state.ip, label)
