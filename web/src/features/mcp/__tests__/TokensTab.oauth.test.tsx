import { afterEach, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { TokensTab } from "@/features/mcp/TokensTab";
import { ClientsTab } from "@/features/mcp/ClientsTab";

afterEach(() => vi.restoreAllMocks());

const now = Date.now() / 1000;
const pat = { id: "p1", kind: "pat", name: "laptop", prefix: "doc4ai_pat_AAAA", scopes: ["doc4ai:read"], note: null, created_at: now - 100,
  expires_at: now + 86400 * 30, revoked_at: null, revoked_reason: null, rotated_from: null, rate_limit_per_min: null, last_used_at: null,
  last_used_ip: null, last_client: null, status: "active", calls_24h: 0, errors_24h: 0 };
const grant = { ...pat, id: "g1", kind: "oauth", name: "Claude Code", prefix: "claude.ai", note: "localhost", created_at: now,
  scopes: ["doc4ai:read", "doc4ai:convert"], expires_at: now + 86400 * 90, refresh_expires_at: now + 86400 * 30,
  oauth: { client_id: "https://claude.ai/oauth/claude-code-client-metadata", kind: "cimd", redirect_uri: "http://localhost:3118/callback",
           access_tokens_active: 1 } };

function setup(tokens: unknown[]) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({ url, init });
    if (url.includes("/revoke")) return new Response(JSON.stringify({ token: { ...grant, status: "revoked" } }), { status: 200 });
    return new Response(JSON.stringify({ tokens }), { status: 200 });
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><TokensTab /></MemoryRouter>
    </QueryClientProvider>,
  );
  return calls;
}

test("an OAuth connection shows its badge and source, and its menu only revokes", async () => {
  const user = userEvent.setup();
  const calls = setup([grant, pat]);
  const row = await screen.findByRole("row", { name: /Claude Code/ });
  expect(within(row).getByText("OAuth", { selector: "span" })).toBeInTheDocument();
  expect(row).toHaveTextContent("來源 claude.ai");
  expect(row).toHaveTextContent("回到 localhost");
  expect(screen.getByText(/用 OAuth 授權連線的用戶端會自動出現在這裡/)).toBeInTheDocument();
  await user.click(within(row).getByRole("button", { name: "動作" }));
  const items = await screen.findAllByRole("menuitem");
  expect(items.map((i) => i.textContent)).toEqual(["撤銷"]);
  await user.click(items[0]);
  expect(await screen.findByText("撤銷後這個用戶端的 access 與 refresh token 立即失效，需要重新授權。")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "確認撤銷" }));
  await waitFor(() => expect(calls.some((c) => c.url.endsWith("/api/mcp/tokens/g1/revoke") && c.init?.method === "POST")).toBe(true));
});

test("a PAT keeps edit, rotate and revoke", async () => {
  const user = userEvent.setup();
  setup([pat]);
  const row = await screen.findByRole("row", { name: /laptop/ });
  expect(within(row).queryByText("OAuth", { selector: "span" })).toBeNull();
  await user.click(within(row).getByRole("button", { name: "動作" }));
  const items = await screen.findAllByRole("menuitem");
  expect(items.map((i) => i.textContent)).toEqual(["編輯名稱與備註", "輪替", "撤銷"]);
});

test("the empty state offers the OAuth way in", async () => {
  setup([]);
  expect(await screen.findByText(/或直接在 Claude Code 加入 http:\/\/127\.0\.0\.1:.*\/mcp，再用瀏覽器授權/)).toBeInTheDocument();
});

test("clients of an OAuth connection carry the pill", async () => {
  const c = { id: "c1", token_id: "g1", token_name: "Claude Code", token_kind: "oauth", client_name: "claude-code", client_version: "2.1",
    protocol_version: "2026-07-28", user_agent: null, first_seen: now - 600, last_seen: now - 400, last_ip: "127.0.0.1", request_count: 3,
    active: false, token_status: "active" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
    new Response(JSON.stringify(String(input).includes("/tokens") ? { tokens: [] } : { clients: [c] }), { status: 200 }));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><ClientsTab /></MemoryRouter>
    </QueryClientProvider>,
  );
  const table = await screen.findByRole("table", { name: "歷史 client" });
  expect(within(table).getByText("OAuth", { selector: "span" })).toBeInTheDocument();
});
