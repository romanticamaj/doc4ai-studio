import json
from pathlib import Path

from aidoc.mcp.snippets import PLACEHOLDER, readme_section, render_snippets

URL = "http://ulove-arrangement.tail74077f.ts.net:3333/mcp"


def test_four_snippets_with_placeholder():
    s = render_snippets(URL)
    assert [x["client"] for x in s] == ["claude-code", "cursor", "vscode", "claude-desktop", "openwebui"]
    mcp = [x for x in s if x["client"] != "openwebui"]           # Open WebUI: the docling-serve compatible API
    assert all(x["title"] and x["language"] in ("bash", "json") and URL in x["text"] for x in mcp)
    assert all(PLACEHOLDER in x["text"] for x in s if x["client"] != "vscode")      # VS Code prompts for the token
    cc = s[0]["text"]
    assert cc.startswith("claude mcp add --transport http doc4ai " + URL) and '--header "Authorization: Bearer <YOUR_TOKEN>"' in cc
    assert "\n" not in cc and "\\" not in cc             # one line: pastes into PowerShell and cmd as well as bash
    cursor = json.loads(s[1]["text"])
    assert cursor["mcpServers"]["doc4ai"] == {"url": URL, "headers": {"Authorization": f"Bearer {PLACEHOLDER}"}}
    vscode = json.loads(s[2]["text"])
    assert vscode["servers"]["doc4ai"]["type"] == "http" and vscode["inputs"][0]["password"] is True
    assert vscode["servers"]["doc4ai"]["headers"]["Authorization"] == "Bearer ${input:doc4ai-token}"
    desk = json.loads(s[3]["text"])["mcpServers"]["doc4ai"]
    assert desk["command"] == "npx" and desk["args"][:3] == ["-y", "mcp-remote", URL] and "--allow-http" in desk["args"]
    assert desk["env"]["DOC4AI_TOKEN"] == f"Bearer {PLACEHOLDER}" and "Authorization:${DOC4AI_TOKEN}" in desk["args"]
    assert all(" " not in a for a in desk["args"])                                  # Windows Desktop quoting rule (research §6)


def test_real_token_is_filled_everywhere():
    s = render_snippets(URL, "doc4ai_pat_ABC")
    assert all("doc4ai_pat_ABC" in x["text"] and PLACEHOLDER not in x["text"] for x in s if x["client"] != "vscode")
    assert "doc4ai_pat_ABC" not in s[2]["text"]                                      # VS Code prompts for it instead


def test_readme_contains_the_rendered_block():
    readme = Path(__file__).resolve().parents[2].joinpath("README.md").read_text(encoding="utf-8")
    block = readme_section("http://<host>:<port>/mcp")
    assert block.strip() in readme and "## MCP" in readme


def test_config_snippets_endpoint(client, ctx):
    ctx.extras["bind_host"], ctx.extras["bind_port"] = "127.0.0.1", 8765
    r = client.get("/api/mcp/config-snippets")
    assert r.status_code == 200 and r.json()["endpoint_url"] == "http://127.0.0.1:8765/mcp"
    assert [s["client"] for s in r.json()["snippets"]] == ["claude-code", "cursor", "vscode", "claude-desktop", "openwebui"]
    assert all(PLACEHOLDER in s["text"] for s in r.json()["snippets"] if s["client"] != "vscode")
    tid = client.post("/api/mcp/tokens", json={"name": "x", "scopes": ["doc4ai:read"]}).json()["record"]["id"]
    assert client.get(f"/api/mcp/config-snippets?token_id={tid}").status_code == 200
    assert client.get("/api/mcp/config-snippets?token_id=nope").status_code == 404
    r = client.get("/api/mcp/config-snippets?endpoint=http://evil/mcp")
    assert r.status_code == 422 and r.json()["error"] == "unknown_endpoint"
    ctx.extras["bind_host"], ctx.extras["bind_port"] = "0.0.0.0", 3333
    ctx.config.mcp.allowed_hosts = ["box.tail74077f.ts.net"]
    r = client.get("/api/mcp/config-snippets").json()
    assert r["endpoint_url"] == "http://box.tail74077f.ts.net:3333/mcp"                # last non-loopback URL
    ctx.config.mcp.allowed_hosts = []


def test_create_token_response_snippets_carry_the_token(client, ctx):
    body = client.post("/api/mcp/tokens", json={"name": "x", "scopes": ["doc4ai:read"]}).json()
    assert body["snippets"] and all(body["token"] in s["text"] for s in body["snippets"] if s["client"] != "vscode")


def test_openwebui_snippet():
    s = next(x for x in render_snippets("http://box:8765/mcp", "doc4ai_pat_X") if x["client"] == "openwebui")
    assert "DOCLING_SERVER_URL=http://box:8765" in s["text"] and "/v1" not in s["text"] and "/mcp" not in s["text"]
    assert "DOCLING_API_KEY=doc4ai_pat_X" in s["text"] and "CONTENT_EXTRACTION_ENGINE=docling" in s["text"]
    assert s["language"] == "bash"
