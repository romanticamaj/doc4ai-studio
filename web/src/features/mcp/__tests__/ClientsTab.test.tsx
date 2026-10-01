import { afterEach, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ClientsTab } from "@/features/mcp/ClientsTab";

afterEach(() => vi.restoreAllMocks());

test("renders active cards and the history table", async () => {
  const now = Date.now() / 1000;
  const clients = [
    { id: "c1", token_id: "t", token_name: "laptop", client_name: "claude-code", client_version: "2.3.1", protocol_version: "2026-07-28", user_agent: "x",
      first_seen: now - 100, last_seen: now - 10, last_ip: "100.64.0.9", request_count: 12, active: true },
    { id: "c2", token_id: "t", token_name: "laptop", client_name: "cursor", client_version: "1.0", protocol_version: "2025-11-25", user_agent: null,
      first_seen: now - 90000, last_seen: now - 80000, last_ip: "10.0.0.2", request_count: 3, active: false },
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    return new Response(JSON.stringify(url.includes("/tokens") ? { tokens: [] } : { clients }), { status: 200 });
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ClientsTab /></QueryClientProvider>);
  await waitFor(() => expect(screen.getByText("claude-code 2.3.1")).toBeInTheDocument());
  const card = screen.getByRole("article", { name: /claude-code 2.3.1/ });
  expect(card).toHaveTextContent("2026-07-28");
  expect(card).toHaveTextContent("100.64.0.9");
  expect(card).toHaveTextContent("12");
  const history = screen.getByRole("table", { name: "歷史 client" });
  expect(history).toHaveTextContent("cursor 1.0");
  expect(history).toHaveTextContent("2025-11-25");
  expect(screen.queryByRole("article", { name: /cursor/ })).toBeNull();
});
