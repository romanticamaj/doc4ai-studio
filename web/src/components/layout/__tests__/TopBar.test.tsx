import { expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { qk } from "@/api/queries";
import { StatusCluster } from "../StatusCluster";
import { TopBar } from "../TopBar";

function renderBar(system: unknown) {
  const qc = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  qc.setQueryData(qk.system(), system);
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <TopBar status={<StatusCluster />} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("shows GPU, memory, queue length and paused badge", () => {
  renderBar({ gpu: { name: "RTX", mem_total: 16e9, mem_used: 4e9 }, queue: { paused: true, length: 3, running_task_id: null } });
  expect(screen.getByText("RTX")).toBeInTheDocument();
  expect(screen.getByText("佇列 3")).toBeInTheDocument();
  expect(screen.getByText("已暫停")).toBeInTheDocument();
  expect(screen.getByText("4.0 / 16.0 GB")).toBeInTheDocument();
});

test("no GPU and a running queue: no memory bar, no paused badge", () => {
  renderBar({ gpu: null, queue: { paused: false, length: 0, running_task_id: null } });
  expect(screen.getByText("佇列 0")).toBeInTheDocument();
  expect(screen.queryByText("已暫停")).toBeNull();
  expect(screen.queryByText(/GB$/)).toBeNull();
});
