"""Client configuration snippets (MCP spec §8.1). The only place that knows each client's syntax; the API, the web
UI and the README all render from here. Sources: research doc §6 (Claude Code, Cursor, VS Code, mcp-remote)."""
from __future__ import annotations

import json

PLACEHOLDER = "<YOUR_TOKEN>"
SERVER_ALIAS = "doc4ai"


def render_snippets(endpoint_url: str, token: str = PLACEHOLDER) -> list[dict]:
    bearer = f"Bearer {token}"
    # one line: a bash "\" continuation breaks when pasted into PowerShell or cmd (this project's host is Windows)
    claude_code = f'claude mcp add --transport http {SERVER_ALIAS} {endpoint_url} --header "Authorization: {bearer}"'
    cursor = {"mcpServers": {SERVER_ALIAS: {"url": endpoint_url, "headers": {"Authorization": bearer}}}}
    vscode = {"servers": {SERVER_ALIAS: {"type": "http", "url": endpoint_url,
                                        "headers": {"Authorization": "Bearer ${input:doc4ai-token}"}}},
              "inputs": [{"id": "doc4ai-token", "type": "promptString", "password": True, "description": "Doc4AI Studio token"}]}
    desktop = {"mcpServers": {SERVER_ALIAS: {"command": "npx",
                                            "args": ["-y", "mcp-remote", endpoint_url, "--header", "Authorization:${DOC4AI_TOKEN}",
                                                     "--allow-http"],
                                            "env": {"DOC4AI_TOKEN": bearer}}}}
    return [
        {"client": "claude-code", "title": "Claude Code", "language": "bash", "text": claude_code},
        {"client": "cursor", "title": "Cursor（~/.cursor/mcp.json）", "language": "json", "text": json.dumps(cursor, indent=2)},
        {"client": "vscode", "title": "VS Code（.vscode/mcp.json）", "language": "json", "text": json.dumps(vscode, indent=2)},
        {"client": "claude-desktop", "title": "Claude Desktop（claude_desktop_config.json，經 mcp-remote）", "language": "json",
         "text": json.dumps(desktop, indent=2)},
        {"client": "openwebui", "title": "Open WebUI（環境變數，docling-serve 相容 API）", "language": "bash",
         "text": openwebui_env(endpoint_url, token)},
    ]


OAUTH_HINT = "然後在 Claude Code 輸入 /mcp → 選 doc4ai → 認證"


def oauth_snippets(endpoint_url: str) -> list[dict]:
    """No token anywhere (local OAuth spec 2026-11-05 §7.2): the client discovers the authorization server from the
    401 and opens the browser on the 授權連線 page."""
    cursor = {"mcpServers": {SERVER_ALIAS: {"url": endpoint_url}}}
    vscode = {"servers": {SERVER_ALIAS: {"type": "http", "url": endpoint_url}}}
    return [
        {"client": "claude-code", "title": "Claude Code", "language": "bash",
         "text": f"claude mcp add --transport http {SERVER_ALIAS} {endpoint_url}\n# {OAUTH_HINT}", "hint": OAUTH_HINT},
        {"client": "cursor", "title": "Cursor（~/.cursor/mcp.json）", "language": "json", "text": json.dumps(cursor, indent=2)},
        {"client": "vscode", "title": "VS Code（.vscode/mcp.json）", "language": "json", "text": json.dumps(vscode, indent=2)},
        {"client": "inspector", "title": "MCP Inspector（Streamable HTTP URL）", "language": "text", "text": endpoint_url},
    ]


def compat_base_url(endpoint_url: str) -> str:
    """The server's origin for the docling-serve compatible API: clients append /v1/... themselves."""
    return endpoint_url.removesuffix("/mcp").rstrip("/")


def openwebui_env(endpoint_url: str, token: str = PLACEHOLDER) -> str:
    """Open WebUI's Docling extraction engine pointed at Doc4AI (compat spec §8.1). No /v1: Open WebUI adds it."""
    return "\n".join(["CONTENT_EXTRACTION_ENGINE=docling", f"DOCLING_SERVER_URL={compat_base_url(endpoint_url)}",
                      f"DOCLING_API_KEY={token}"])


MCP_CLIENTS = ("claude-code", "cursor", "vscode", "claude-desktop")


def readme_section(endpoint_url: str) -> str:
    fence = "`" * 3
    parts = ["## MCP", "",
             ("Doc4AI Studio serves an MCP endpoint at `/mcp` (Streamable HTTP, Bearer personal access tokens). Create a token in "
              "the web UI (MCP → Tokens); it is shown once. Then connect a client:"), ""]
    for s in render_snippets(endpoint_url):
        if s["client"] not in MCP_CLIENTS:
            continue
        parts += [f"**{s['title']}**", "", f"{fence}{s['language']}", s["text"], fence, ""]
    parts += [("Tokens never go in the URL; the endpoint answers `401` with `WWW-Authenticate: Bearer realm=\"doc4ai\"` when one "
               "is missing or revoked (after 10 failures a minute from one address: `429` with the same reason, for a "
               "minute). Scopes: `doc4ai:read`, `doc4ai:convert`, `doc4ai:convert:local`, `doc4ai:manage`."), ""]
    return "\n".join(parts)
