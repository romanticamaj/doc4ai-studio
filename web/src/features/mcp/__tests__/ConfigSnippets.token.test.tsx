import { afterEach, expect, test, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { OverviewTab } from "@/features/mcp/OverviewTab";

const TOKEN = "doc4ai_pat_xGW2n8zGcJdieMqBmDCjiu10YwpYVPWuO187gPXtTYj_450QG8";
const URL_ = "http://box.tail74077f.ts.net:3333/mcp";
const status = { enabled: true, endpoint_urls: [URL_], bind: { host: "0.0.0.0", port: 3333 }, allowed_hosts: [], protocol_versions: ["2026-07-28"],
  sdk_version: "2.2.0", active_clients: 0, calls_24h: 0, errors_24h: 0, tokens_expiring_soon: 0, local_path_roots: [], plaintext_http: true, tokenizer: "trigram" };
const stats = { window: "24h", since: 0, tools: [], tokens: [], series: [] };
const VSCODE_TEXT = '{"Authorization": "Bearer ${input:doc4ai-token}"}';
const snippets = [
  { client: "claude-code", title: "Claude Code", language: "bash", text: `claude mcp add --transport http doc4ai ${URL_} --header "Authorization: Bearer <YOUR_TOKEN>"` },
  { client: "cursor", title: "Cursor（~/.cursor/mcp.json）", language: "json", text: '{"Authorization": "Bearer <YOUR_TOKEN>"}' },
  { client: "vscode", title: "VS Code（.vscode/mcp.json）", language: "json", text: VSCODE_TEXT },
  { client: "claude-desktop", title: "Claude Desktop（claude_desktop_config.json，經 mcp-remote）", language: "json", text: '{"DOC4AI_TOKEN": "Bearer <YOUR_TOKEN>"}' },
];

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  if (originalClipboard) Object.defineProperty(navigator, "clipboard", originalClipboard);
  else delete (navigator as { clipboard?: unknown }).clipboard;
});

function setup() {
  const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    const body = url.includes("/stats") ? stats : url.includes("/config-snippets") ? { endpoint_url: URL_, snippets } : status;
    return new Response(JSON.stringify(body), { status: 200 });
  });
  const ui = (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <OverviewTab />
      </MemoryRouter>
    </QueryClientProvider>
  );
  const r = render(ui);
  return { fetchSpy, ...r, ui };
}

const box = () => screen.findByLabelText("貼上你的 MCP token 以填入設定");
const visibleSnippet = () => document.querySelector("[role=tabpanel][data-state=active] pre")?.textContent ?? "";

test("a pasted PAT is filled into the snippets and the copy button copies the filled text", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  setup();
  const input = await box();
  expect(input).toHaveAttribute("type", "password");
  expect(input).toHaveAttribute("autocomplete", "off");
  expect(screen.getByText(/不會傳到伺服器/)).toBeInTheDocument();
  await screen.findByRole("tab", { name: "Claude Code" });
  await userEvent.click(input);
  await userEvent.paste(TOKEN);
  expect(visibleSnippet()).toContain(`Bearer ${TOKEN}`);
  expect(visibleSnippet()).not.toContain("<YOUR_TOKEN>");
  await userEvent.click(within(document.querySelector<HTMLElement>("[role=tabpanel][data-state=active]")!).getByRole("button", { name: "複製" }));
  expect(writeText).toHaveBeenCalledWith(expect.stringContaining(`Bearer ${TOKEN}`));
  for (const name of [/Cursor/, /Claude Desktop/]) {
    await userEvent.click(screen.getByRole("tab", { name }));
    expect(visibleSnippet()).toContain(`Bearer ${TOKEN}`);
  }
  await userEvent.click(screen.getByRole("tab", { name: /VS Code/ }));
  expect(visibleSnippet()).toBe(VSCODE_TEXT); // VS Code prompts for the token itself; it is not written to the file
  expect(screen.getByText(/VS Code 會在第一次連線時詢問 token/)).toBeInTheDocument();
});

test("show / hide toggles the field type", async () => {
  setup();
  const input = await box();
  await userEvent.click(screen.getByRole("button", { name: "顯示 token" }));
  expect(input).toHaveAttribute("type", "text");
  await userEvent.click(screen.getByRole("button", { name: "隱藏 token" }));
  expect(input).toHaveAttribute("type", "password");
});

test("a non-PAT string (the server token) gets a gentle explanation and the placeholder stays", async () => {
  setup();
  const input = await box();
  await screen.findByRole("tab", { name: "Claude Code" });
  await userEvent.click(input);
  await userEvent.paste("my-server-api-token");
  expect(screen.getByRole("alert")).toHaveTextContent(/doc4ai_pat_/);
  expect(screen.getByRole("alert")).toHaveTextContent(/伺服器/);
  expect(visibleSnippet()).toContain("<YOUR_TOKEN>");
  await userEvent.clear(input);
  await userEvent.paste(TOKEN.replace("xGW2", "xGW3"));
  expect(screen.getByRole("alert")).toHaveTextContent(/不完整|打錯/);
  expect(visibleSnippet()).toContain("<YOUR_TOKEN>");
});

test("the token never leaves the page: no request carries it and nothing stores it", async () => {
  const setItem = vi.spyOn(Storage.prototype, "setItem");
  const { fetchSpy } = setup();
  const input = await box();
  await screen.findByRole("tab", { name: "Claude Code" });
  await userEvent.click(input);
  await userEvent.paste(TOKEN);
  await userEvent.click(screen.getByRole("tab", { name: /Cursor/ }));
  await new Promise((r) => setTimeout(r, 50));
  expect(fetchSpy).toHaveBeenCalled();
  for (const [req, init] of fetchSpy.mock.calls) {
    expect(String(req)).not.toContain(TOKEN);
    expect(JSON.stringify(init ?? {})).not.toContain(TOKEN);
  }
  for (const call of setItem.mock.calls) expect(JSON.stringify(call)).not.toContain(TOKEN);
  for (const store of [localStorage, sessionStorage])
    for (let i = 0; i < store.length; i++) expect(store.getItem(store.key(i)!) ?? "").not.toContain(TOKEN);
  expect(document.cookie).not.toContain(TOKEN);
});

test("the token is gone after the page is left and opened again", async () => {
  const { unmount, ui } = setup();
  const input = await box();
  await userEvent.click(input);
  await userEvent.paste(TOKEN);
  unmount();
  render(ui);
  expect(((await box()) as HTMLInputElement).value).toBe("");
  expect(document.body.textContent).not.toContain(TOKEN);
});
