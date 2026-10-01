import { expect, test } from "vitest";
import { mergeLiveCall } from "@/features/mcp/mergeCalls";
import type { McpCall, McpCallsPage } from "@/api/types";

const call = (id: number, extra: Partial<McpCall> = {}): McpCall =>
  ({ id, ts: id, token_id: "t1", token_prefix_seen: null, client_id: "c1", method: "tools/call", tool_name: "read_document",
     resource_uri: null, args_summary: "{}", status: "ok", error_code: null, http_status: 200, duration_ms: 5, response_bytes: 10,
     response_tokens_est: 3, ip: "::1", protocol_version: "2026-07-28", job_id: null, token_name: "a", client_name: "cc", ...extra }) as McpCall;

test("inserts at the top, dedupes and caps", () => {
  const pages: McpCallsPage[] = [{ calls: [call(2), call(1)], next_cursor: "1" }];
  const out = mergeLiveCall(pages, call(3), {})!;
  expect(out[0].calls.map((c) => c.id)).toEqual([3, 2, 1]);
  expect(mergeLiveCall(out, call(3), {})![0].calls.map((c) => c.id)).toEqual([3, 2, 1]);
  const big: McpCallsPage[] = [{ calls: Array.from({ length: 3 }, (_, i) => call(10 - i)), next_cursor: null }];
  expect(mergeLiveCall(big, call(11), {}, 3)![0].calls.map((c) => c.id)).toEqual([11, 10, 9]);
});

test("respects the active filters", () => {
  const pages: McpCallsPage[] = [{ calls: [], next_cursor: null }];
  expect(mergeLiveCall(pages, call(1), { tool: "search_library" })![0].calls).toEqual([]);
  expect(mergeLiveCall(pages, call(1), { tool: "read_document", status: "ok", token_id: "t1", client_id: "c1" })![0].calls).toHaveLength(1);
  expect(mergeLiveCall(pages, call(1, { status: "auth_error" }), { status: "ok" })![0].calls).toEqual([]);
  expect(mergeLiveCall(undefined, call(1), {})).toBeUndefined();
});

test("an update to a known row merges in place and keeps fields the event does not carry", () => {
  const pages: McpCallsPage[] = [{ calls: [call(3), call(2, { args_summary: '{"suppressed": 4}', status: "rate_limited" }), call(1)], next_cursor: null }];
  const ev = { id: 2, ts: 2, status: "rate_limited", error_code: "auth_failures" } as unknown as McpCall;
  const out = mergeLiveCall(pages, ev, {})!;
  expect(out[0].calls.map((c) => c.id)).toEqual([3, 2, 1]);
  expect(out[0].calls[1].args_summary).toBe('{"suppressed": 4}');
  expect(out[0].calls[1].error_code).toBe("auth_failures");
});
