import { afterEach, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { OfficePreview } from "../OfficePreview";
import type { PreviewState } from "@/api/types";

afterEach(() => vi.restoreAllMocks());

function setup(preview: PreviewState, response: unknown = { preview: { status: "pending" } }) {
  const fetchSpy = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response(JSON.stringify(response), { status: 202 }));
  const renderPdf = vi.fn((url: string) => <div data-testid="pdf">{url}</div>);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <OfficePreview docId="d1" preview={preview} version={12.5} sourceUrl="/api/documents/d1/source" ext="docx" renderPdf={renderPdf} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { fetchSpy, renderPdf };
}

test("a document without a preview asks the server for one once", async () => {
  const { fetchSpy } = setup({ status: "none" });
  await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
  const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
  expect(url).toContain("/api/documents/d1/preview");
  expect(init.method).toBe("POST");
  expect(screen.getByText(/正在產生預覽/)).toBeInTheDocument();
});

test("pending shows progress and does not post again", async () => {
  const { fetchSpy } = setup({ status: "pending" });
  expect(screen.getByText(/正在產生預覽/)).toBeInTheDocument();
  await new Promise((r) => setTimeout(r, 20));
  expect(fetchSpy).not.toHaveBeenCalled();
});

test("ready renders the preview PDF through the PDF viewer", () => {
  const { renderPdf } = setup({ status: "ready", pages: 4 });
  expect(renderPdf).toHaveBeenCalled();
  const url = renderPdf.mock.calls[0][0];
  expect(url).toMatch(/^\/api\/documents\/d1\/preview\?v=12\.5/);
  expect(screen.getByTestId("pdf")).toBeInTheDocument();
});

test("missing LibreOffice explains it, links to Settings and keeps the original downloadable", () => {
  setup({ status: "failed", reason: "soffice_missing" });
  expect(screen.getByText(/沒有安裝 LibreOffice/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Settings/ })).toHaveAttribute("href", "/settings#office-preview");
  expect(screen.getByRole("link", { name: /下載原始檔/ })).toHaveAttribute("href", "/api/documents/d1/source");
});

test("a failed conversion can be retried and keeps the original downloadable", async () => {
  const { fetchSpy } = setup({ status: "failed", reason: "timeout" });
  expect(screen.getByText(/預覽產生失敗/)).toBeInTheDocument();
  expect(screen.getByText(/逾時/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /下載原始檔/ })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /再試一次/ }));
  await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
  const init = fetchSpy.mock.calls[0][1] as RequestInit;
  expect(JSON.parse(String(init.body))).toEqual({ retry: true });
});
