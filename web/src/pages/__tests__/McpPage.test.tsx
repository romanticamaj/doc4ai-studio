import { afterEach, expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import McpPage from "@/pages/McpPage";

afterEach(() => vi.restoreAllMocks());

function Where() {
  const l = useLocation();
  return <output data-testid="where">{l.search}</output>;
}

function setup(initial = "/mcp") {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    const body = url.includes("/tokens") ? { tokens: [] } : url.includes("/clients") ? { clients: [] } : url.includes("/calls")
      ? { calls: [], next_cursor: null } : url.includes("/stats") ? { window: "24h", since: 0, tools: [], tokens: [], series: [] }
      : url.includes("/config-snippets") ? { endpoint_url: "http://127.0.0.1:8765/mcp", snippets: [] }
      : { enabled: true, endpoint_urls: ["http://127.0.0.1:8765/mcp"], bind: { host: "127.0.0.1", port: 8765 }, allowed_hosts: [],
          protocol_versions: ["2026-07-28"], sdk_version: "2.2.0", active_clients: 0, calls_24h: 0, errors_24h: 0, tokens_expiring_soon: 0,
          local_path_roots: [], plaintext_http: true, tokenizer: "trigram" };
    return new Response(JSON.stringify(body), { status: 200 });
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/mcp" element={<><McpPage /><Where /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("renders the four tabs and keeps the active tab in the URL", async () => {
  setup();
  expect(screen.getByRole("heading", { name: "MCP" })).toBeInTheDocument();
  for (const name of ["總覽", "Tokens", "連線", "呼叫紀錄"]) expect(screen.getByRole("tab", { name })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "總覽" })).toHaveAttribute("aria-selected", "true");
  await userEvent.click(screen.getByRole("tab", { name: "Tokens" }));
  expect(screen.getByTestId("where").textContent).toBe("?tab=tokens");
});

test("opens on the tab named in the URL", () => {
  setup("/mcp?tab=calls");
  expect(screen.getByRole("tab", { name: "呼叫紀錄" })).toHaveAttribute("aria-selected", "true");
});
