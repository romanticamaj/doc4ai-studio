"""Local MCP OAuth (spec 2026-11-05): an embedded OAuth 2.1 authorization server for /mcp on the same origin.

metadata  issuer / PRM / AS metadata derived from the validated Host
tokens    token and client id formats (prefix + base62 body + CRC, HMAC-hashed like PATs)
clients   client registry: CIMD fetch (SSRF-guarded, cached, stale fallback), DCR, redirect rules
grants    authorization codes, grants, access / refresh tokens, revocation
consent   the 授權連線 consent page and its signed authorization request
endpoints the FastAPI router for /oauth/*"""
