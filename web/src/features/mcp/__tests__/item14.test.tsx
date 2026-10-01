import { afterEach, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { applyEvent } from "@/events/applyEvent";
import { qk } from "@/api/mcp";
import type { AidocEvent, McpClient } from "@/api/types";
import { splitClients } from "@/features/mcp/clientState";
import { TokensTab } from "@/features/mcp/TokensTab";
import { ClientsTab } from "@/features/mcp/ClientsTab";
import { CallsTab } from "@/features/mcp/CallsTab";
import { TokenRevealDialog } from "@/features/mcp/TokenRevealDialog";

afterEach(() => vi.restoreAllMocks());

const now = Date.now() / 1000;
const client = (extra: Partial<McpClient> = {}): McpClient => ({ id: "c1", token_id: "t1", token_name: "laptop", client_name: "claude-code",
  client_version: "2.3", protocol_version: "2026-07-28", user_agent: null, first_seen: now - 60, last_seen: now - 10, last_ip: "::1",
  request_count: 3, active: true, token_status: "active", ...extra });

function mockFetch(body: (url: string) => unknown) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => new Response(JSON.stringify(body(String(input))), { status: 200 }));
}

const wrap = (ui: React.ReactNode) =>
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);

test("token statuses read in Chinese (有效／已過期／已撤銷)", async () => {
  const t = (id: string, status: string) => ({ id, name: `n-${id}`, prefix: "doc4ai_pat_AAAA", scopes: ["doc4ai:read"], note: null, created_at: now,
    expires_at: now + 86400 * 30, revoked_at: null, revoked_reason: null, rotated_from: null, rate_limit_per_min: null, last_used_at: null,
    last_used_ip: null, last_client: null, status, calls_24h: 0, errors_24h: 0 });
  mockFetch(() => ({ tokens: [t("a", "active"), t("b", "expired"), t("c", "revoked")] }));
  wrap(<TokensTab />);
  await waitFor(() => expect(screen.getByRole("row", { name: /n-a/ })).toHaveTextContent("有效"));
  expect(screen.getByRole("row", { name: /n-b/ })).toHaveTextContent("已過期");
  expect(screen.getByRole("row", { name: /n-c/ })).toHaveTextContent("已撤銷");
});

test("clients of a revoked token leave 活躍 at once, live and on refetch", () => {
  expect(splitClients([client({ token_status: "revoked" })], now).active).toHaveLength(0);
  const qc = new QueryClient();
  qc.setQueryData(qk.mcp.clients({}), { clients: [client()] });
  applyEvent(qc, { kind: "mcp.token", seq: 1, payload: { id: "t1", name: "laptop", prefix: "doc4ai_pat_AAAA", action: "revoked" } } as AidocEvent);
  const c = qc.getQueryData<{ clients: McpClient[] }>(qk.mcp.clients({}))!.clients[0];
  expect(c.token_status).toBe("revoked");
  expect(splitClients([c], now).active).toHaveLength(0);
});

test("client cards say 活躍 and the history marks a revoked token", async () => {
  mockFetch((url) => (url.includes("/tokens") ? { tokens: [] } : { clients: [client(), client({ id: "c2", client_name: "cursor", token_status: "revoked" })] }));
  wrap(<ClientsTab />);
  const card = await screen.findByRole("article", { name: /claude-code/ });
  expect(card).toHaveTextContent("活躍");
  expect(screen.getByRole("table", { name: "歷史 client" })).toHaveTextContent("已撤銷");
});

test("the two time selectors have distinct names", async () => {
  mockFetch((url) => (url.includes("/calls") ? { calls: [], next_cursor: null } : url.includes("/stats")
    ? { window: "24h", since: 0, tools: [], tokens: [], series: [] } : url.includes("/tokens") ? { tokens: [] } : { clients: [] }));
  wrap(<CallsTab />);
  expect(await screen.findByRole("radiogroup", { name: "時間篩選" })).toBeInTheDocument();
  expect(screen.getByRole("radiogroup", { name: "統計區間" })).toBeInTheDocument();
});

test("the revealed token wraps instead of being cut off", () => {
  const result = { token: "doc4ai_pat_" + "x".repeat(43) + "_abcdef", record: { id: "t", name: "n" }, snippets: [] } as never;
  render(<TokenRevealDialog result={result} onClose={() => {}} />);
  const field = screen.getByRole("textbox", { name: /token/ });
  expect(field.tagName).toBe("TEXTAREA");
  expect(field.className).toMatch(/break-all/);
});
