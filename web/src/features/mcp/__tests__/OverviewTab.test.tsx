import { afterEach, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { OverviewTab } from "@/features/mcp/OverviewTab";

afterEach(() => vi.restoreAllMocks());

const status = { enabled: true, endpoint_urls: ["http://100.64.0.9:3333/mcp", "http://box.tail0.ts.net:3333/mcp"], bind: { host: "0.0.0.0", port: 3333 },
  allowed_hosts: [], protocol_versions: ["2026-07-28", "2025-11-25"], sdk_version: "2.2.0", active_clients: 2, calls_24h: 40, errors_24h: 4,
  tokens_expiring_soon: 1, local_path_roots: [], plaintext_http: true, tokenizer: "trigram" };
const stats = { window: "24h", since: 0, tools: [{ tool: "read_document", calls: 30, errors: 2, error_rate: 0.07, p50_ms: 20, p95_ms: 120, tokens_median: 900 }],
  tokens: [], series: Array.from({ length: 24 }, (_, i) => ({ ts: i, calls: i % 5, errors: 0 })) };

function setup() {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    const body = url.includes("/stats") ? stats : url.includes("/config-snippets")
      ? { endpoint_url: "http://box.tail0.ts.net:3333/mcp", snippets: [{ client: "claude-code", title: "Claude Code", language: "bash", text: "claude mcp add ... <YOUR_TOKEN>" }] }
      : status;
    return new Response(JSON.stringify(body), { status: 200 });
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><OverviewTab /></MemoryRouter>
    </QueryClientProvider>,
  );
}

test("shows status, metrics, notices and snippets", async () => {
  setup();
  await waitFor(() => expect(screen.getByText("啟用中")).toBeInTheDocument());
  expect(screen.getByText("http://box.tail0.ts.net:3333/mcp")).toBeInTheDocument();
  expect(screen.getByText("2026-07-28")).toBeInTheDocument();
  expect(screen.getByText("2.2.0")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("10%")).toBeInTheDocument());        // 4 / 40
  expect(screen.getByText("120 ms")).toBeInTheDocument();
  expect(screen.getByRole("img", { name: /24 小時呼叫/ })).toBeInTheDocument();
  expect(screen.getByText(/明文傳輸/)).toBeInTheDocument();
  expect(screen.getByText(/未設定（convert_path 隱藏）/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /1 把 token 將在 14 天內到期/ })).toHaveAttribute("href", "/mcp?tab=tokens");
  await waitFor(() => expect(screen.getByRole("tab", { name: "Claude Code" })).toBeInTheDocument());
  expect(screen.getByText(/<YOUR_TOKEN>/)).toBeInTheDocument();
});
