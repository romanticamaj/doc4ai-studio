import { afterEach, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import SettingsPage from "@/pages/SettingsPage";

afterEach(() => vi.restoreAllMocks());

const settings = {
  general: { output_dir: "out", lang: "cht", work_retention_days: 7, enable_audio: false },
  engines: { mineru_tier: "basic", docling_ocr: "rapidocr", docling_page_batch_size: 16 },
  limits: { upload_max_bytes: 1, timeout_per_page_s: 60, timeout_min_s: 120, timeout_per_mb_s: 30, timeout_max_no_pages_s: 1800, startup_timeout_s: 900, disk_space_factor: 3 },
  server: { host: "127.0.0.1", port: 8765, token: "" },
  mcp: { enabled: true, allowed_hosts: [], local_path_roots: [], default_token_ttl_days: 90, max_token_ttl_days: 365, allow_no_expiry: false,
    rate_limit_per_min: 60, max_concurrent_jobs_per_token: 3, max_upload_mb: 20, response_token_budget: 8000, call_log_retention_days: 30, call_log_max_rows: 200000 },
};

test("the MCP section edits [mcp] and sends only the changed keys", async () => {
  const puts: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes("/api/settings") && init?.method === "PUT") {
      puts.push(JSON.parse(String(init.body)));
      return new Response(JSON.stringify({ settings }), { status: 200 });
    }
    if (url.includes("/api/settings")) return new Response(JSON.stringify({ settings }), { status: 200 });
    return new Response(JSON.stringify({ engines: {}, gpu: null, queue: { paused: false, length: 0, running_task_id: null }, disk_free: 1, output_dir: "out", version: "0.1.0", long_paths_enabled: true }), { status: 200 });
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><SettingsPage /></MemoryRouter></QueryClientProvider>);
  await waitFor(() => expect(screen.getByRole("heading", { name: "MCP" })).toBeInTheDocument());
  const roots = await screen.findByLabelText("convert_path 白名單根目錄");      // the form renders once settings load
  await userEvent.type(roots, "D:\\docs");
  await userEvent.clear(screen.getByLabelText("回應預算（tokens）"));
  await userEvent.type(screen.getByLabelText("回應預算（tokens）"), "6000");
  await userEvent.click(screen.getAllByRole("button", { name: "儲存" }).at(-1)!);
  await waitFor(() => expect(puts).toHaveLength(1));
  expect(puts[0]).toEqual({ mcp: { local_path_roots: ["D:\\docs"], response_token_budget: 6000 } });
});
