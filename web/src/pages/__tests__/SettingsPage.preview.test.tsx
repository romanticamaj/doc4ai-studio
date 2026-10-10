import { afterEach, expect, test, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import SettingsPage from "@/pages/SettingsPage";

afterEach(() => vi.restoreAllMocks());

function setup(libreoffice: unknown) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input).includes("/api/settings")) return new Response(JSON.stringify({ error: "x" }), { status: 500 });
    return new Response(
      JSON.stringify({ engines: {}, gpu: null, queue: { paused: false, length: 0, running_task_id: null }, disk_free: 1, output_dir: "out", version: "0.1.0", long_paths_enabled: true, libreoffice }),
      { status: 200 },
    );
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("LibreOffice found: version and path are shown", async () => {
  setup({ found: true, path: "C:\Program Files\LibreOffice\program\soffice.exe", version: "26.8.0.3", configured: false });
  const panel = await screen.findByTestId("office-preview");
  expect(panel.id).toBe("office-preview");
  expect(await within(panel).findByText("26.8.0.3")).toBeInTheDocument();
  expect(within(panel).getByText(/soffice\.exe/)).toBeInTheDocument();
  expect(within(panel).getByText(/已偵測到/)).toBeInTheDocument();
});

test("LibreOffice missing: says so and how to fix it", async () => {
  setup({ found: false, path: null, version: null, configured: false });
  const panel = await screen.findByTestId("office-preview");
  expect(await within(panel).findByText(/未偵測到 LibreOffice/)).toBeInTheDocument();
  expect(within(panel).getByText(/soffice_path/)).toBeInTheDocument();
});
