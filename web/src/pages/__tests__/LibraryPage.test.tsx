import { afterEach, expect, test, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
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
