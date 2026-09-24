import { expect, test, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { buildJobRequest } from "@/features/convert/buildJobRequest";
import type { UploadItem } from "@/upload/useUploads";

test("builds job body from form state", () => {
  expect(
    buildJobRequest({ uploadIds: ["u1"], paths: ["C:\\docs\\a.pdf", ""], engine: "auto", lang: "cht", force: false, retryLow: true, allowOnlineAudio: false }),
  ).toEqual({ inputs: [{ upload_id: "u1" }, { path: "C:\\docs\\a.pdf" }], lang: "cht", force: false, retry_low: true, allow_online_audio: false });
  expect(
    buildJobRequest({ uploadIds: [], paths: ["x"], engine: "mineru", lang: "en", force: true, retryLow: false, allowOnlineAudio: true }).engine,
  ).toBe("mineru");
});

test("paths are trimmed, and surrounding quotes from Explorer's 'Copy as path' are removed", () => {
  const body = buildJobRequest({ uploadIds: [], paths: ['  "C:\\a b\\c.pdf"  ', "   "], engine: "auto", lang: "cht", force: false, retryLow: false, allowOnlineAudio: false });
  expect(body.inputs).toEqual([{ path: "C:\\a b\\c.pdf" }]);
});

// ---- render: useUploads is mocked so the test controls the phase
const state = { items: [] as UploadItem[] };
const add = vi.fn((files: File[]) => {
  state.items = Array.from(files).map((file, i) => ({ key: `k${i}`, file, phase: "uploading", progress: 0.4 }));
});
vi.mock("@/upload/useUploads", () => ({
  useUploads: () => ({ items: state.items, add, cancel: vi.fn(), retry: vi.fn(), clear: vi.fn() }),
}));

test("a dropped file shows a row and submit stays disabled until the upload is done", async () => {
  const { default: ConvertPage } = await import("@/pages/ConvertPage");
  const ui = () => (
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <ConvertPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
  const { rerender } = render(ui());
  const submit = () => screen.getByRole("button", { name: "開始轉換" });
  expect(submit()).toBeDisabled();

  const f = new File([new Uint8Array(2048)], "report.pdf");
  fireEvent.drop(screen.getByTestId("dropzone"), { dataTransfer: { files: [f], types: ["Files"] } });
  expect(add).toHaveBeenCalled();
  rerender(ui());
  expect(screen.getByText("report.pdf")).toBeInTheDocument();
  expect(screen.getByText("上傳中")).toBeInTheDocument();
  expect(submit()).toBeDisabled();

  state.items = [{ ...state.items[0], phase: "done", progress: 1, upload_id: "u1" }];
  rerender(ui());
  expect(screen.getByText("完成")).toBeInTheDocument();
  expect(submit()).toBeEnabled();
});
