import { afterEach, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { CallsTab } from "@/features/mcp/CallsTab";

afterEach(() => vi.restoreAllMocks());

const calls = [
  { id: 2, ts: 1700000000, token_id: "t", token_name: "laptop", client_id: "c", client_name: "claude-code", method: "tools/call", tool_name: "read_document",
    resource_uri: null, args_summary: '{"doc_id":"d","pages":"1-3"}', status: "ok", error_code: null, http_status: 200, duration_ms: 42, response_bytes: 8200,
    response_tokens_est: 1900, ip: "100.64.0.9", protocol_version: "2026-07-28", job_id: null, token_prefix_seen: null },
  { id: 1, ts: 1699999000, token_id: null, token_name: null, client_id: null, client_name: null, method: null, tool_name: null, resource_uri: null, args_summary: null,
    status: "auth_error", error_code: "revoked", http_status: 401, duration_ms: null, response_bytes: null, response_tokens_est: null, ip: "::1",
    protocol_version: "2026-07-28", job_id: null, token_prefix_seen: "doc4ai_pat_3kX9" },
];

test("lists calls, expands a row and shows per-tool stats", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    const body = url.includes("/api/mcp/calls") ? { calls, next_cursor: null } : url.includes("/stats")
      ? { window: "24h", since: 0, tools: [{ tool: "read_document", calls: 10, errors: 1, error_rate: 0.1, p50_ms: 40, p95_ms: 90, tokens_median: 1800 }], tokens: [], series: [] }
      : url.includes("/tokens") ? { tokens: [] } : { clients: [] };
    return new Response(JSON.stringify(body), { status: 200 });
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/mcp?tab=calls"]}><CallsTab /></MemoryRouter>
    </QueryClientProvider>,
  );
  const callsTable = () => screen.getByRole("table", { name: "呼叫紀錄" });
  await waitFor(() => expect(within(callsTable()).getByText("read_document")).toBeInTheDocument());
  const rows = screen.getAllByRole("row");
  expect(rows.some((r) => r.textContent?.includes("auth_error") && r.textContent?.includes("doc4ai_pat_3kX9"))).toBe(true);
  await userEvent.click(within(callsTable()).getByText("read_document"));
  expect(screen.getByText(/"pages":"1-3"/)).toBeInTheDocument();
  expect(screen.getByText("100.64.0.9")).toBeInTheDocument();
  const stats = screen.getByRole("table", { name: "每個 tool 的統計" });
  expect(stats).toHaveTextContent("10%");
  expect(stats).toHaveTextContent("90");
});
