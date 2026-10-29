import { afterEach, expect, test, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { RootDirField } from "@/features/settings/RootDirField";
import { ROOT_CHECK_DEBOUNCE_MS } from "@/api/queries";
import type { SystemInfo } from "@/api/types";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const sys = {
  root_dir: "C:\\doc4ai",
  root_configured: false,
  root_from_env: false,
  root_default: "C:\\doc4ai",
  source_dir: "C:\\doc4ai\\source",
} as SystemInfo;

type Check = { ok: boolean; problem: string | null; root_dir: string; output_dir: string | null; source_dir: string | null };

function mockCheck(answer: (v: string) => Check) {
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input).includes("/api/settings/root-check")) {
      const v = JSON.parse(String(init?.body)).root_dir as string;
      calls.push(v);
      return new Response(JSON.stringify({ exists: false, free_bytes: 1, ...answer(v) }), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  });
  return calls;
}

function Harness({ initial = "", saved = "", info = sys, onValidity = () => {} }: { initial?: string; saved?: string; info?: SystemInfo; onValidity?: (ok: boolean) => void }) {
  const [v, setV] = useState(initial);
  return (
    <>
      <RootDirField value={v} saved={saved} sys={info} onChange={setV} onValidity={onValidity} />
      <button type="button" onClick={() => setV("D:\\doc4ai")}>set-valid</button>
      <button type="button" onClick={() => setV("relative\\x")}>set-bad</button>
    </>
  );
}

function renderField(props: Parameters<typeof Harness>[0] = {}) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Harness {...props} />
    </QueryClientProvider>,
  );
}

test("shows the resolved 轉換結果 / 原始檔備份 paths from the root check", async () => {
  mockCheck((v) => ({ ok: true, problem: null, root_dir: v, output_dir: `${v}\\out`, source_dir: `${v}\\source` }));
  renderField({ initial: "D:\\doc4ai" });
  await waitFor(() => expect(screen.getByTestId("root-out")).toHaveTextContent("D:\\doc4ai\\out"));
  expect(screen.getByText("轉換結果")).toBeInTheDocument();
  expect(screen.getByText("原始檔備份")).toBeInTheDocument();
  expect(screen.getByTestId("root-source")).toHaveTextContent("D:\\doc4ai\\source");
});

test("a problem shows the red text and makes the form unsavable", async () => {
  mockCheck(() => ({ ok: false, problem: "必須是本機的絕對路徑", root_dir: "relative\\x", output_dir: null, source_dir: null }));
  const validity: boolean[] = [];
  renderField({ initial: "relative\\x", onValidity: (ok) => validity.push(ok) });
  expect(await screen.findByRole("alert")).toHaveTextContent("必須是本機的絕對路徑");
  expect(validity.at(-1)).toBe(false);
});

test("unset: the platform default is the placeholder and the grey note says so", () => {
  mockCheck(() => ({ ok: true, problem: null, root_dir: "", output_dir: null, source_dir: null }));
  renderField({ initial: "" });
  const input = screen.getByRole("textbox");
  expect(input).toHaveAttribute("placeholder", "C:\\doc4ai");
  expect(screen.getByText(/尚未設定：使用預設的 Doc4AI 資料夾/)).toHaveTextContent("C:\\doc4ai");
  expect(screen.getByTestId("root-out")).toHaveTextContent("C:\\doc4ai\\out");
  expect(screen.getByTestId("root-source")).toHaveTextContent("C:\\doc4ai\\source");
});

test("root_from_env: read-only with the environment note", () => {
  mockCheck(() => ({ ok: true, problem: null, root_dir: "", output_dir: null, source_dir: null }));
  renderField({ info: { ...sys, root_from_env: true, root_dir: "E:\\lib" } as SystemInfo });
  const input = screen.getByRole("textbox");
  expect(input).toHaveAttribute("readonly");
  expect(input).toHaveValue("E:\\lib");
  expect(screen.getByText("由環境變數 AIDOC_LIBRARY_ROOT 指定")).toBeInTheDocument();
  expect(screen.getByTestId("root-out")).toHaveTextContent("E:\\lib\\out");
});

test("no request before 400 ms of idle typing", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const calls = mockCheck((v) => ({ ok: true, problem: null, root_dir: v, output_dir: `${v}\\out`, source_dir: `${v}\\source` }));
  renderField({ initial: "" });
  act(() => screen.getByText("set-valid").click());
  await act(async () => {
    vi.advanceTimersByTime(ROOT_CHECK_DEBOUNCE_MS - 50);
  });
  expect(calls).toEqual([]);
  await act(async () => {
    vi.advanceTimersByTime(100);
  });
  await waitFor(() => expect(calls).toEqual(["D:\\doc4ai"]));
  expect(ROOT_CHECK_DEBOUNCE_MS).toBe(400);
});
