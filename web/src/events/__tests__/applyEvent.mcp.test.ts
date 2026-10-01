import { expect, test } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { applyEvent } from "@/events/applyEvent";
import { qk } from "@/api/mcp";
import type { AidocEvent, McpClient } from "@/api/types";

const qc = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

test("mcp.call is merged into the calls cache and bumps status/stats", () => {
  const c = qc();
  c.setQueryData(qk.mcp.calls({}), { pages: [{ calls: [], next_cursor: null }], pageParams: [undefined] });
  c.setQueryData(qk.mcp.status(), { calls_24h: 0 });
  const ev = { kind: "mcp.call", seq: 1, payload: { id: 7, ts: 1, token_id: "t", token_name: "a", client_id: "c", client_name: "cc",
    method: "tools/call", tool_name: "read_document", status: "ok", error_code: null, http_status: 200, duration_ms: 3, response_tokens_est: 2, job_id: null } } as AidocEvent;
  applyEvent(c, ev);
  const data = c.getQueryData<{ pages: { calls: { id: number }[] }[] }>(qk.mcp.calls({}))!;
  expect(data.pages[0].calls[0].id).toBe(7);
  expect(c.getQueryState(qk.mcp.status())?.isInvalidated).toBe(true);
});

test("mcp.client upserts and mcp.token invalidates", () => {
  const c = qc();
  const old: McpClient = { id: "c1", token_id: "t", token_name: "a", client_name: "cc", client_version: "1", protocol_version: "2026-07-28",
    user_agent: null, first_seen: 1, last_seen: 1, last_ip: "::1", request_count: 1, active: true };
  c.setQueryData(qk.mcp.clients({}), { clients: [old] });
  c.setQueryData(qk.mcp.tokens(), { tokens: [] });
  applyEvent(c, { kind: "mcp.client", seq: 2, payload: { ...old, last_seen: 9, request_count: 2, state: "active" } } as AidocEvent);
  expect(c.getQueryData<{ clients: McpClient[] }>(qk.mcp.clients({}))!.clients[0].request_count).toBe(2);
  applyEvent(c, { kind: "mcp.client", seq: 3, payload: { ...old, id: "c2", client_name: "cursor", state: "new" } } as AidocEvent);
  expect(c.getQueryData<{ clients: McpClient[] }>(qk.mcp.clients({}))!.clients.map((x) => x.id)).toEqual(["c2", "c1"]);
  applyEvent(c, { kind: "mcp.token", seq: 4, payload: { id: "t", name: "a", prefix: "doc4ai_pat_AAAA", action: "revoked" } } as AidocEvent);
  expect(c.getQueryState(qk.mcp.tokens())?.isInvalidated).toBe(true);
});

test("mcp.call bumps the client's request count and last request, and the token's last use and 24h counts", () => {
  const c = qc();
  const client: McpClient = { id: "c9", token_id: "t9", token_name: "a", client_name: "cc", client_version: "1", protocol_version: "2026-07-28",
    user_agent: null, first_seen: 1, last_seen: 1, last_ip: "::1", request_count: 4, active: true };
  c.setQueryData(qk.mcp.clients({}), { clients: [client] });
  c.setQueryData(qk.mcp.tokens(), [{ id: "t9", name: "a", last_used_at: 1, last_used_ip: null, calls_24h: 4, errors_24h: 0 }]);
  const ev = (id: number, status = "ok") => ({ kind: "mcp.call", seq: id, payload: { id, ts: 500, token_id: "t9", token_name: "a", client_id: "c9",
    client_name: "cc", method: "tools/call", tool_name: "read_document", status, error_code: null, http_status: 200, duration_ms: 3,
    response_tokens_est: 2, job_id: null, ip: "100.64.0.9" } } as AidocEvent);
  applyEvent(c, ev(901));
  applyEvent(c, ev(901));                                       // the same row re-published (auth-flood aggregate) counts once
  applyEvent(c, ev(902, "tool_error"));
  const cl = c.getQueryData<{ clients: McpClient[] }>(qk.mcp.clients({}))!.clients[0];
  expect(cl.request_count).toBe(6);
  expect(cl.last_seen).toBe(500);
  const tok = c.getQueryData<{ calls_24h: number; errors_24h: number; last_used_at: number; last_used_ip: string }[]>(qk.mcp.tokens())![0];
  expect([tok.calls_24h, tok.errors_24h, tok.last_used_at, tok.last_used_ip]).toEqual([6, 1, 500, "100.64.0.9"]);
});
