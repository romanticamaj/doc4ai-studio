import { afterEach, expect, test, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { EventStreamProvider } from "../EventStreamProvider";

class FakeES {
  static last: FakeES;
  static CLOSED = 2;
  readyState = 1;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  url: string;
  constructor(url: string) {
    this.url = url;
    FakeES.last = this;
  }
  addEventListener() {}
  close() {}
}

afterEach(() => vi.unstubAllGlobals());

test("after the stream drops and comes back, jobs, documents and system are refetched (I1)", () => {
  vi.stubGlobal("EventSource", FakeES);
  const qc = new QueryClient();
  const spy = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <EventStreamProvider>
        <div />
      </EventStreamProvider>
    </QueryClientProvider>,
  );
  act(() => FakeES.last.onopen?.());
  expect(spy).not.toHaveBeenCalled(); // the first connection needs no refetch
  act(() => FakeES.last.onerror?.());
  act(() => FakeES.last.onopen?.());
  expect(spy).toHaveBeenCalledWith({ queryKey: ["jobs"] });
  expect(spy).toHaveBeenCalledWith({ queryKey: ["documents"] });
  expect(spy).toHaveBeenCalledWith({ queryKey: ["system"] });
});
