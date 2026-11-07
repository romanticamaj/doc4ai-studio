# Security policy

## Report a problem

Please report security problems privately through [GitHub's advisory form](https://github.com/romanticamaj/doc4ai-studio/security/advisories/new). Do not open a public issue.

Include the version (`doc4ai --version`), what an attacker can do, and how to reproduce it. Do not send real documents or live tokens. You can expect a first reply within 7 days. This is a project maintained in spare time, so a fix may take longer, and we will tell you where it stands.

## Supported versions

Only the latest release receives security fixes.

## What is in scope

Doc4AI processes documents you may not trust, and it can be reached by AI clients. These matter most:

- Bypassing authentication or scopes on `/mcp`, the OAuth flow or the docling-serve compatible API
- Reading or writing files outside the Doc4AI folder
- Code execution through a crafted document
- Token leakage through logs, responses or the web studio
- Document content sent to an external service without the user turning that on

Vulnerabilities in MarkItDown, Docling or MinerU themselves belong with those projects. Tell us as well if Doc4AI's use of them makes the problem worse.

## Running it safely

- The server binds to `127.0.0.1` unless you choose otherwise. If you expose it to a network, set a server token and put it behind TLS.
- Give each MCP client its own token with the narrowest scopes it needs, and revoke tokens you no longer use.
- Remote VLM OCR is off by default. Turning it on sends page images to the endpoint you configure.
- Text extracted from a document is untrusted. An AI client reading it can be exposed to prompt injection placed in that document.
