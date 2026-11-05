import { afterEach, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { OverviewTab } from "@/features/mcp/OverviewTab";

afterEach(() => vi.restoreAllMocks());

const URL_ = "http://127.0.0.1:3333/mcp";
const base = { enabled: true, endpoint_urls: [URL_], bind: { host: "127.0.0.1", port: 3333 }, allowed_hosts: [], protocol_versions: ["2026-07-28"],
  sdk_version: "2.2.0", active_clients: 0, calls_24h: 0, errors_24h: 0, tokens_expiring_soon: 0, local_path_roots: [], plaintext_http: true,
  tokenizer: "trigram", oauth: { enabled: true, allow_dcr: true, allow_cimd: true, issuer_example: "http://127.0.0.1:3333", grants_active: 0, clients: 0 } };
const OAUTH = [{ client: "claude-code", title: "Claude Code", language: "bash", hint: "然後在 Claude Code 輸入 /mcp → 選 doc4ai → 認證",
  text: `claude mcp add --transport http doc4ai ${URL_}\n# 然後在 Claude Code 輸入 /mcp → 選 doc4ai → 認證` }];
const PAT = [{ client: "claude-code", title: "Claude Code", language: "bash",
  text: `claude mcp add --transport http doc4ai ${URL_} --header "Authorization: Bearer <YOUR_TOKEN>"` }];

function setup(status: object) {
  const urls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    urls.push(url);
    const body = url.includes("/stats") ? { window: "24h", since: 0, tools: [], tokens: [], series: [] }
      : url.includes("/config-snippets") ? { endpoint_url: URL_, snippets: url.includes("auth=oauth") ? OAUTH : PAT }
      : status;
    return new Response(JSON.stringify(body), { status: 200 });
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><OverviewTab /></MemoryRouter>
    </QueryClientProvider>,
  );
  return urls;
}

test("OAuth is the default: no token field, the two-step hint; PAT brings the placeholder back", async () => {
  const user = userEvent.setup();
  const urls = setup(base);
  const oauth = await screen.findByRole("radio", { name: "OAuth（瀏覽器授權）" });
  expect(oauth).toHaveAttribute("aria-checked", "true");
  await waitFor(() => expect(screen.getAllByText(/然後在 Claude Code 輸入 \/mcp → 選 doc4ai → 認證/).length).toBeGreaterThan(0));
  expect(screen.queryByText(/<YOUR_TOKEN>/)).toBeNull();
  expect(screen.queryByLabelText(/貼上 token/)).toBeNull();
  expect(screen.getByText(/不需要先建 token/)).toBeInTheDocument();
  expect(screen.queryByText(/別台電腦上的 Claude Code/)).toBeNull();               // loopback server: no notice
  expect(urls.some((u) => u.includes("/config-snippets") && u.includes("auth=oauth"))).toBe(true);
  await user.click(screen.getByRole("radio", { name: "PAT" }));
  await waitFor(() => expect(screen.getByText(/<YOUR_TOKEN>/)).toBeInTheDocument());
  expect(screen.getByRole("radio", { name: "PAT" })).toHaveAttribute("aria-checked", "true");
});

test("token mode warns that remote Claude Code cannot use http OAuth", async () => {
  setup({ ...base, bind: { host: "0.0.0.0", port: 3333 } });
  expect(await screen.findByText("別台電腦上的 Claude Code 無法用 http 的 OAuth；請用 PAT")).toBeInTheDocument();
});

test("a server without OAuth shows the PAT snippets and no toggle", async () => {
  const { oauth: _drop, ...old } = base;
  setup(old);
  await waitFor(() => expect(screen.getByText(/<YOUR_TOKEN>/)).toBeInTheDocument());
  expect(screen.queryByRole("radio", { name: "PAT" })).toBeNull();
});
