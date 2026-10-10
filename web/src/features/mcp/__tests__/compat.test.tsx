import { afterEach, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { OverviewTab } from "@/features/mcp/OverviewTab";
import { originLabel } from "@/features/jobs/originLabel";
import { SCOPE_META } from "@/features/mcp/tokenStatus";

afterEach(() => vi.restoreAllMocks());

const base = { enabled: true, endpoint_urls: ["http://h:8765/mcp"], bind: { host: "0.0.0.0", port: 8765 }, allowed_hosts: [],
  protocol_versions: ["2026-07-28"], sdk_version: "2.2.0", active_clients: 0, calls_24h: 0, errors_24h: 0,
  tokens_expiring_soon: 0, local_path_roots: [], plaintext_http: true, tokenizer: "trigram" };

function setup(compat: unknown) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    const body = url.includes("/stats") ? { window: "24h", since: 0, tools: [], tokens: [], series: [] }
      : url.includes("/config-snippets") ? { endpoint_url: "http://h:8765/mcp", snippets: [] }
      : { ...base, compat };
    return new Response(JSON.stringify(body), { status: 200 });
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><OverviewTab /></MemoryRouter>
    </QueryClientProvider>,
  );
}

test("overview shows the docling-serve compatible API when it is enabled", async () => {
  setup({ enabled: true, base_url: "http://h:8765", calls_24h: 3 });
  await waitFor(() => expect(screen.getByText("docling-serve 相容 API")).toBeInTheDocument());
  expect(screen.getByText("http://h:8765")).toBeInTheDocument();
  expect(screen.getByText(/24 小時 3 次/)).toBeInTheDocument();
});

test("overview hides the compatible API line when it is disabled", async () => {
  setup({ enabled: false, base_url: "http://h:8765", calls_24h: 0 });
  await waitFor(() => expect(screen.getByText("啟用中")).toBeInTheDocument());
  expect(screen.queryByText("docling-serve 相容 API")).not.toBeInTheDocument();
});

test("jobs show compat as 相容 API and keep the other origins", () => {
  expect(originLabel("compat")).toBe("相容 API");
  expect(originLabel("mcp")).toBe("mcp");
  expect(originLabel("web")).toBe("web");
});

test("the convert scope says it also opens the compatible API", () => {
  expect(SCOPE_META["doc4ai:convert"].hint).toMatch(/docling-serve 相容 API/);
});
