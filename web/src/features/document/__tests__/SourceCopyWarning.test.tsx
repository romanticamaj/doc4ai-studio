import { afterEach, expect, test, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { copyReason, detailText, SourceCopyWarning } from "@/features/document/SourceCopyWarning";
import { qk } from "@/api/queries";
import type { DocumentDetail, SourceCopyDetail } from "@/api/types";

afterEach(() => vi.restoreAllMocks());

const sc = (o: Partial<SourceCopyDetail>): SourceCopyDetail => ({ status: "failed", error: null, present: false, path: null, size: null, copied_at: null, ...o });

test("the reason follows the error code (spec 2026-10-29 §11.2)", () => {
  const cases: [Partial<SourceCopyDetail>, string | null][] = [
    [{ error: "no_space: D:\\doc4ai\\source: 1 bytes free" }, "磁碟空間不足"],
    [{ error: "unreadable: C:\\a.pdf" }, "無法讀取原始檔"],
    [{ status: "unrecoverable", error: "sha_mismatch: C:\\a.pdf" }, "內容與轉換時不同"],
    [{ error: "collision: taken" }, "備份位置已被其他檔案佔用"],
    [{ error: "io_error: [errno 17] File exists" }, "寫入失敗"],
    [{ status: "unrecoverable", error: "missing_original" }, "原始檔不在了"],
    [{ status: null }, "尚未補齊備份"],
    [{ status: "copied", present: false }, "備份檔不見了"],
    [{ status: "copied", present: true }, null],
  ];
  for (const [o, want] of cases) expect(copyReason(sc(o))).toBe(want);
});

function setup(detail: SourceCopyDetail, retry: () => Response) {
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    calls.push(`${init?.method ?? "GET"} ${String(input)}`);
    if (String(input).endsWith("/source-copy")) return retry();
    return new Response("{}", { status: 404 });
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData<DocumentDetail>(qk.document("d1"), { source_copy: detail } as DocumentDetail);
  function View() {
    const d = qc.getQueryData<DocumentDetail>(qk.document("d1"));
    return <SourceCopyWarning docId="d1" sourceCopy={d?.source_copy} />;
  }
  const r = render(
    <QueryClientProvider client={qc}>
      <View />
    </QueryClientProvider>,
  );
  return { calls, qc, r, View };
}

test("shows 原始檔未備份：磁碟空間不足 and 重試備份 hides it on 200", async () => {
  const { calls, qc, r, View } = setup(sc({ error: "no_space: full" }), () =>
    new Response(JSON.stringify({ outcome: "copied", source_copy: sc({ status: "copied", present: true }) }), { status: 200 }),
  );
  expect(screen.getByTestId("source-copy-warning")).toHaveTextContent("原始檔未備份：磁碟空間不足");
  await userEvent.click(screen.getByRole("button", { name: /重試備份/ }));
  await waitFor(() => expect(calls).toContain("POST /api/documents/d1/source-copy"));
  await waitFor(() => expect(qc.getQueryData<DocumentDetail>(qk.document("d1"))?.source_copy?.status).toBe("copied"));
  r.rerender(
    <QueryClientProvider client={qc}>
      <View />
    </QueryClientProvider>,
  );
  expect(screen.queryByTestId("source-copy-warning")).not.toBeInTheDocument();
});

test("nothing is shown for a good copy", () => {
  setup(sc({ status: "copied", present: true }), () => new Response("{}"));
  expect(screen.queryByTestId("source-copy-warning")).not.toBeInTheDocument();
});

test("the warning carries the real reason after the plain text", () => {
  setup(sc({ error: "io_error: [errno 17] File exists: source\\sample" }), () => new Response("{}"));
  expect(screen.getByTestId("source-copy-warning")).toHaveTextContent("原始檔未備份：寫入失敗（[errno 17] File exists: source\\sample）");
});

test("detailText translates every code and keeps the detail", () => {
  expect(detailText("missing_original")).toBe("原始檔不在了");
  expect(detailText("sha_mismatch: C:\\x.pdf")).toBe("內容與轉換時不同：C:\\x.pdf");
  expect(detailText("unreadable: C:\\x.pdf")).toBe("無法讀取原始檔：C:\\x.pdf");
  expect(detailText("no_space: full")).toBe("磁碟空間不足：full");
  expect(detailText("collision: taken")).toBe("備份位置已被其他檔案佔用：taken");
  expect(detailText(null)).toBe("");
});
