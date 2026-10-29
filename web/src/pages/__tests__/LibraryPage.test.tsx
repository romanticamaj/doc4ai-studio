import { afterEach, expect, test, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import LibraryPage from "@/pages/LibraryPage";

afterEach(() => vi.restoreAllMocks());

function Where() {
  const l = useLocation();
  return <output data-testid="where">{l.search}</output>;
}

function setup(initial = "/library") {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ documents: [] }), { status: 200 }));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/library" element={<><LibraryPage /><Where /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return screen.getByRole("searchbox", { name: "搜尋檔名" }) as HTMLInputElement;
}

const wait = (ms: number) => act(() => new Promise((r) => setTimeout(r, ms)));

test("typing keeps every character and reaches the URL after a short pause", async () => {
  const box = setup();
  await userEvent.type(box, "report 2026");
  expect(box.value).toBe("report 2026");
  await wait(400);
  expect(screen.getByTestId("where").textContent).toBe("?q=report+2026");
});

test("an IME composition is not written to the URL until it ends", async () => {
  const box = setup("/library?view=table");
  expect(box.value).toBe("");
  fireEvent.compositionStart(box);
  fireEvent.change(box, { target: { value: "ㄅㄠˋ" } });
  await wait(400);
  expect(screen.getByTestId("where").textContent).toBe("?view=table");
  expect(box.value).toBe("ㄅㄠˋ");
  fireEvent.change(box, { target: { value: "報告" } });
  fireEvent.compositionEnd(box);
  await wait(400);
  expect(screen.getByTestId("where").textContent).toBe("?q=%E5%A0%B1%E5%91%8A&view=table");
});

test("Back/forward to a URL with another query updates the box", async () => {
  const box = setup("/library?q=abc");
  expect(box.value).toBe("abc");
});

test("a filter picked while the search is still pending is kept", async () => {
  const box = setup();
  await userEvent.type(box, "text");
  await userEvent.click(screen.getByRole("radio", { name: "表格" }));
  await wait(400);
  expect(screen.getByTestId("where").textContent).toBe("?q=text&view=table");
});

// ---- spec 2026-10-29 §11.3: 未備份 banner, purge confirmation

function setupData(opts: { notCopied?: number; orphans?: number; copies?: number; bytes?: number }) {
  const calls: string[] = [];
  const doc = (id: string, status = "ok", flags: string[] = []) => ({
    id, sha256: "x", source_path: `C:/in/${id}.pdf`, output_dir: `C:/out/${id}`, engine: "docling", pages: 1, lang: "cht",
    aidoc_version: "0.1.0", created_at: 1, status, stem: id, flags, page_summary: null,
    quality: { score: 1, level: "ok", reasons: [], metrics: {} }, source_copy: { status: null, present: false },
  });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push(`${init?.method ?? "GET"} ${url}`);
    if (init?.method === "DELETE") return new Response(JSON.stringify({ deleted: opts.orphans ?? 0, source_copies_deleted: opts.copies ?? 0, source_copy_bytes: 0, source_copies_kept: [] }), { status: 200 });
    if (url.startsWith("/api/sources")) return new Response(JSON.stringify({ root_dir: "C:/doc4ai", source_dir: "C:/doc4ai/source", documents: 0, copied: 0, not_copied: 0, failed: 0, unrecoverable: 0, file_missing: 0, bytes: 0, free_bytes: 1, orphaned_copies: { count: opts.copies ?? 0, bytes: opts.bytes ?? 0 } }), { status: 200 });
    if (url.includes("flag=source_not_copied")) return new Response(JSON.stringify({ documents: Array.from({ length: opts.notCopied ?? 0 }, (_, i) => doc(`n${i}`, "ok", ["source_not_copied"])) }), { status: 200 });
    if (url.includes("status=orphaned")) return new Response(JSON.stringify({ documents: Array.from({ length: opts.orphans ?? 0 }, (_, i) => doc(`o${i}`, "orphaned")) }), { status: 200 });
    return new Response(JSON.stringify({ documents: [] }), { status: 200 });
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/library"]}>
        <Routes>
          <Route path="/library" element={<><LibraryPage /><Where /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return calls;
}

test("the 未備份 banner counts the documents and links to the filter and to Settings", async () => {
  setupData({ notCopied: 3 });
  const banner = await screen.findByTestId("source-copy-banner");
  expect(banner).toHaveTextContent("有 3 份文件的原始檔沒有備份");
  expect(screen.getByRole("link", { name: "前往補齊" })).toHaveAttribute("href", "/settings#source-copies");
  await userEvent.click(screen.getByRole("button", { name: "只看這些" }));
  expect(screen.getByTestId("where").textContent).toBe("?flag=source_not_copied");
});

test("清除 opens the confirmation; only 清除 inside it deletes", async () => {
  const calls = setupData({ orphans: 2, copies: 2, bytes: 3 * 1024 * 1024 });
  await userEvent.click(await screen.findByRole("button", { name: "清除" }));
  const dialog = await screen.findByTestId("confirm-purge");
  expect(dialog).toHaveTextContent("清除 2 筆紀錄？");
  expect(dialog).toHaveTextContent("也會刪除這些文件在 source\\ 的原始檔備份（2 個檔案，3.0 MB）");
  expect(calls.some((c) => c.startsWith("DELETE"))).toBe(false);
  await userEvent.click(within(dialog).getByRole("button", { name: "清除" }));
  await waitFor(() => expect(calls).toContain("DELETE /api/documents/orphaned"));
});

test("without copies the second sentence is absent", async () => {
  setupData({ orphans: 1, copies: 0 });
  await userEvent.click(await screen.findByRole("button", { name: "清除" }));
  const dialog = await screen.findByTestId("confirm-purge");
  expect(dialog).toHaveTextContent("清除 1 筆紀錄？");
  expect(dialog).not.toHaveTextContent("原始檔備份");
});
