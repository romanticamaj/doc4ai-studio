# MCP server

Doc4AI Studio serves an MCP endpoint at `/mcp` (Streamable HTTP, Bearer personal access tokens). Create a token in the web UI (MCP → Tokens); it is shown once. Then connect a client:

**Claude Code**

```bash
claude mcp add --transport http doc4ai http://<host>:<port>/mcp --header "Authorization: Bearer <YOUR_TOKEN>"
```

**Cursor（~/.cursor/mcp.json）**

```json
{
  "mcpServers": {
    "doc4ai": {
      "url": "http://<host>:<port>/mcp",
      "headers": {
        "Authorization": "Bearer <YOUR_TOKEN>"
      }
    }
  }
}
```

**VS Code（.vscode/mcp.json）**

```json
{
  "servers": {
    "doc4ai": {
      "type": "http",
      "url": "http://<host>:<port>/mcp",
      "headers": {
        "Authorization": "Bearer ${input:doc4ai-token}"
      }
    }
  },
  "inputs": [
    {
      "id": "doc4ai-token",
      "type": "promptString",
      "password": true,
      "description": "Doc4AI Studio token"
    }
  ]
}
```

**Claude Desktop（claude_desktop_config.json，經 mcp-remote）**

```json
{
  "mcpServers": {
    "doc4ai": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "http://<host>:<port>/mcp",
        "--header",
        "Authorization:${DOC4AI_TOKEN}",
        "--allow-http"
      ],
      "env": {
        "DOC4AI_TOKEN": "Bearer <YOUR_TOKEN>"
      }
    }
  }
}
```

Tokens never go in the URL; the endpoint answers `401` with `WWW-Authenticate: Bearer realm="doc4ai"` when one is missing or revoked (after 10 failures a minute from one address: `429` with the same reason, for a minute). Scopes: `doc4ai:read`, `doc4ai:convert`, `doc4ai:convert:local`, `doc4ai:manage`.

## Connect without a token: browser authorization (OAuth)

MCP clients that support OAuth (Claude Code, Cursor, VS Code, the MCP Python SDK, MCP Inspector) can connect with **no token at all**. Add the endpoint, then authorize it once in the browser:

```bash
claude mcp add --transport http doc4ai http://127.0.0.1:8765/mcp
```

Then in Claude Code type `/mcp`, choose `doc4ai`, and pick 認證 (Authenticate). Your browser opens Doc4AI's 「授權連線」 page: it shows the client's self-reported name (marked 未驗證), where it comes from (bold host such as `claude.ai`, or 「本機註冊的用戶端」), the exact address the browser returns to, and the permissions. `doc4ai:read` and `doc4ai:convert` are ticked; `doc4ai:convert:local` and `doc4ai:manage` carry a small ! mark and stay off unless you tick them. Press 允許連線 and the browser hands the connection back to Claude Code. Cursor (`~/.cursor/mcp.json`) and VS Code (`.vscode/mcp.json`) need only the URL:

```json
{ "mcpServers": { "doc4ai": { "url": "http://127.0.0.1:8765/mcp" } } }
```

```json
{ "servers": { "doc4ai": { "type": "http", "url": "http://127.0.0.1:8765/mcp" } } }
```

How it works: the `401` from `/mcp` names the protected-resource metadata (`WWW-Authenticate: Bearer realm="doc4ai", resource_metadata=".../.well-known/oauth-protected-resource/mcp", scope="doc4ai:read doc4ai:convert"`); the client finds the authorization server on the same origin, identifies itself by its client metadata document (Claude Code) or registers itself (Cursor, the SDK, Inspector), and uses PKCE (S256 only). Access tokens last 60 minutes and refresh on their own; a connection idle for 30 days, or 90 days old, authorizes again. Each connection appears in the MCP page's Tokens list as an `OAuth` row (撤銷 there, `claude mcp logout`, or a reused refresh token cuts it off at the next request), and the 連線 and 呼叫紀錄 tabs name it. Settings → 「OAuth 授權」 holds the switches and lifetimes; `[oauth] enabled = false` in `doc4ai.toml` turns it off and brings back the token-only `401`.

Limits:

- A token is bound to the address you added: `127.0.0.1`, `localhost` and `[::1]` count as three different servers. Use `http://127.0.0.1:<port>/mcp` everywhere.
- Claude Code only sends OAuth credentials to an HTTPS or loopback token endpoint, so Claude Code on another machine cannot use OAuth over plain http (token mode, LAN or tailnet address): use a PAT there. The SDK and MCP Inspector are not limited this way.
- Claude Desktop reaches remote MCP servers only through claude.ai's cloud connectors (public HTTPS), which a local `http://127.0.0.1` server is not; keep the `mcp-remote` + PAT setup above for it.
- PATs keep working exactly as before; switching a client to OAuth does not revoke its old token (revoke it in the Tokens list when you no longer need it).

Local threat model: when the server runs without a server token (loopback only), anyone at this computer's browser may approve the 授權連線 page; nobody else can. A web page open in your browser can still make the browser *request* things from this server (show you the consent or error page, try a cross-site post, point a client id at its own client document), but it cannot get a connection out of it:

- it cannot read the consent page (no CORS) or its signed one-time form field, and the page refuses to be framed;
- a cross-site consent post, and a cross-site or non-JSON client registration, is refused (403 / 400) before it counts against any quota, and junk registrations make room for a real one rather than locking it out;
- a client document that answers slowly cannot hold the server: the fetch has one 5-second deadline, at most 3 run at once, failures are remembered for 30 s, and they run off the request threads;
- before you approve anything, errors are sent back only to a loopback redirect address: never to a web site or an app handler named by an unverified client (no open redirect);
- a DNS-rebinding Host, or a Host with another port than the server's, never becomes an issuer.

Another program on this computer can approve its own connection, exactly as it could already create a PAT through the local admin API; OAuth does not widen that. In token mode (`--host 0.0.0.0` with a server token) the page also asks for the server token: each page allows 5 wrong tokens, and wrong guesses from one address are slowed down step by step (1 to 8 s per attempt), so guessing stays slow without letting a neighbour on the same network lock you out.

## Tools

MCP tools: `search_library`, `get_document_info`, `read_document` (page ranges; every response is capped at a token budget and continues with a cursor), `get_chunks`, `list_documents`, `convert_document`, `get_job`, `cancel_job`, `reconvert_document`, and `convert_path` (only when `[mcp] local_path_roots` is set). The web studio's MCP page lists tokens, connected clients and the call log; its Overview has a field where you can paste a token to fill the snippets above in the browser (the token is never sent or stored).
