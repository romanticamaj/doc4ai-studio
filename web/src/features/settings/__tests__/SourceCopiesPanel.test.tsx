import { afterEach, expect, test, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SourceCopiesPanel } from "@/features/settings/SourceCopiesPanel";
import { qk } from "@/api/queries";
import { applyEvent } from "@/events/applyEvent";
import type { BackfillOutcome, BackfillReport } from "@/api/types";

afterEach(() => vi.restoreAllMocks());

const summary = {
  root_dir: "D:\\doc4ai", source_dir: "D:\\doc4ai\\source", documents: 25, copied: 18, not_copied: 7, failed: 1,
  unrecoverable: 4, file_missing: 2, bytes: 3_145_728, free_bytes: 10_000_000_000,
};

const OUTCOMES: BackfillOutcome[] = ["copied", "already_present", "missing_original", "sha_mismatch", "unreadable", "failed", "skipped_orphaned"];

function report(dry: boolean): BackfillReport {
  return {
    dry_run: dry, source_dir: summary.source_dir, started_at: 0, finished_at: 1, bytes_needed: 0, bytes_copied: 0, free_bytes: 1,
    unreferenced_files: 0,
    totals: Object.fromEntries(OUTCOMES.map((o) => [o, 1])) as Record<BackfillOutcome, number>,
    documents: OUTCOMES.map((o, i) => ({ id: `d${i}`, stem: `doc-${o}`, outcome: o, from: o === "copied" ? "work_copy" : null, size: 1, detail: o === "sha_mismatch" ? "sha_mismatch: C:\\x.pdf" : null })),
  };
}

function setup(answer: (dry: boolean) => Response | Promise<Response>) {
  const posts: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes("/api/sources/backfill")) {
      const body = JSON.parse(String(init?.body));
      posts.push(body);
      return answer(body.dry_run);
    }
    if (url.includes("/api/sources")) return new Response(JSON.stringify(summary), { status: 200 });
    return new Response("{}", { status: 404 });
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SourceCopiesPanel />
    </QueryClientProvider>,
  );
  return { posts, qc };
}

test("renders the four counts", async () => {
  setup(() => new Response("{}"));
  await waitFor(() =>
    expect(screen.getByTestId("source-copy-stats")).toHaveTextContent("已備份 18 份／未備份 7 份／無法還原 4 份／檔案遺失 2 份"),
  );
  expect(screen.getByTestId("source-copy-stats")).toHaveTextContent("3.0 MB");
});

test("檢查（不寫入） posts dry_run true and shows 將備份", async () => {
  const { posts } = setup((dry) => new Response(JSON.stringify(report(dry)), { status: 200 }));
  await userEvent.click(screen.getByRole("button", { name: "檢查（不寫入）" }));
  const table = await screen.findByTestId("backfill-report");
  expect(posts).toEqual([{ dry_run: true }]);
  expect(within(table).getAllByText("將備份").length).toBeGreaterThan(0);
  expect(within(table).getByText("工作副本")).toBeInTheDocument();
  expect(within(table).getByText(/沒有寫入任何檔案/)).toBeInTheDocument();
});

test("補齊備份 posts dry_run false and renders all seven outcome texts", async () => {
  const { posts } = setup((dry) => new Response(JSON.stringify(report(dry)), { status: 200 }));
  await userEvent.click(screen.getByRole("button", { name: "補齊備份" }));
  const table = await screen.findByTestId("backfill-report");
  expect(posts).toEqual([{ dry_run: false }]);
  const rows = within(table).getAllByRole("row");
  const text = rows.map((r) => r.textContent).join("|");
  for (const t of ["已備份", "已存在", "原始檔不在了", "原始檔已被修改（內容與轉換時不同）", "無法讀取（可能被其他程式開著）", "失敗", "略過（輸出已不存在）"]) {
    expect(text).toContain(t);
  }
});

test("409 says a backfill is already running", async () => {
  setup(() => new Response(JSON.stringify({ error: "backfill_running" }), { status: 409 }));
  await userEvent.click(screen.getByRole("button", { name: "補齊備份" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("已有補齊在進行中");
});

test("progress text from the sources.backfill events", async () => {
  let release: (r: Response) => void = () => {};
  const { qc } = setup(() => new Promise<Response>((res) => (release = res)));
  await userEvent.click(screen.getByRole("button", { name: "補齊備份" }));
  act(() => applyEvent(qc, { kind: "sources.backfill", seq: 1, payload: { dry_run: false, done: 3, total: 25 } }));
  expect(await screen.findByTestId("backfill-progress")).toHaveTextContent("處理中 3 / 25");
  expect(qc.getQueryData(qk.sourcesProgress())).toEqual({ dry_run: false, done: 3, total: 25 });
  await act(async () => release(new Response(JSON.stringify(report(false)), { status: 200 })));
  await waitFor(() => expect(screen.queryByTestId("backfill-progress")).not.toBeInTheDocument());
});
