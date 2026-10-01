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
    ]


def readme_section(endpoint_url: str) -> str:
    fence = "`" * 3
    parts = ["## MCP", "",
             ("Doc4AI Studio serves an MCP endpoint at `/mcp` (Streamable HTTP, Bearer personal access tokens). Create a token in "
              "the web UI (MCP → Tokens); it is shown once. Then connect a client:"), ""]
    for s in render_snippets(endpoint_url):
        parts += [f"**{s['title']}**", "", f"{fence}{s['language']}", s["text"], fence, ""]
    parts += [("Tokens never go in the URL; the endpoint answers `401` with `WWW-Authenticate: Bearer realm=\"doc4ai\"` when one "
               "is missing or revoked (after 10 failures a minute from one address: `429` with the same reason, for a "
               "minute). Scopes: `doc4ai:read`, `doc4ai:convert`, `doc4ai:convert:local`, `doc4ai:manage`."), ""]
    return "\n".join(parts)
