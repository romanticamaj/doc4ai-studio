import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { expect, test, vi } from "vitest";
import { applyEvent } from "../applyEvent";
import { qk } from "@/api/queries";
import { LogStore } from "../useTaskLogs";

const task = (over = {}) => ({
  id: "t1",
  job_id: "j1",
  status: "queued",
  tried: [],
  attempt: 0,
  segments: [],
  progress: { pages_done: 0, pages_total: null },
  ...over,
});

test("task.updated replaces task inside job detail and updates list progress", () => {
  const qc = new QueryClient();
  qc.setQueryData(qk.job("j1"), { job: { id: "j1", status: "running", progress: { done: 0, total: 1 } }, tasks: [task()] });
  applyEvent(qc, { kind: "task.updated", seq: 2, payload: task({ status: "converting", engine: "docling" }) } as any);
  expect((qc.getQueryData(qk.job("j1")) as any).tasks[0]).toMatchObject({ status: "converting", engine: "docling" });
});

test("task.updated keeps the segments it does not carry and counts finished tasks", () => {
  const qc = new QueryClient();
  const segs = [{ id: "s0", idx: 0, status: "done" }];
  qc.setQueryData(qk.job("j1"), { job: { id: "j1", status: "running", progress: { done: 0, total: 1 } }, tasks: [task({ segments: segs })] });
  const { segments: _drop, ...noSegs } = task({ status: "done" }) as any;
  applyEvent(qc, { kind: "task.updated", seq: 2, payload: noSegs } as any);
  const d = qc.getQueryData(qk.job("j1")) as any;
  expect(d.tasks[0].segments).toEqual(segs);
  expect(d.job.progress).toEqual({ done: 1, total: 1 });
});

test("task.updated for an unknown task is appended", () => {
  const qc = new QueryClient();
  qc.setQueryData(qk.job("j1"), { job: { id: "j1", progress: { done: 0, total: 1 } }, tasks: [task()] });
  applyEvent(qc, { kind: "task.updated", seq: 3, payload: task({ id: "t2" }) } as any);
  expect((qc.getQueryData(qk.job("j1")) as any).tasks.map((t: any) => t.id)).toEqual(["t1", "t2"]);
});

test("segment.updated updates nested segment", () => {
  const qc = new QueryClient();
  qc.setQueryData(qk.job("j1"), { job: { id: "j1" }, tasks: [task({ segments: [{ id: "s0", idx: 0, status: "queued" }] })] });
  applyEvent(qc, { kind: "segment.updated", seq: 3, payload: { id: "s0", idx: 0, status: "done", task_id: "t1" } } as any);
  expect((qc.getQueryData(qk.job("j1")) as any).tasks[0].segments[0].status).toBe("done");
});

test("segment.updated recomputes page progress", () => {
  const qc = new QueryClient();
  const segs = [
    { id: "s0", idx: 0, page_start: 1, page_end: 40, status: "converting" },
    { id: "s1", idx: 1, page_start: 41, page_end: 45, status: "queued" },
  ];
  qc.setQueryData(qk.job("j1"), { job: { id: "j1" }, tasks: [task({ segments: segs })] });
  applyEvent(qc, { kind: "segment.updated", seq: 4, payload: { ...segs[0], status: "done", task_id: "t1" } } as any);
  expect((qc.getQueryData(qk.job("j1")) as any).tasks[0].progress).toEqual({ pages_done: 40, pages_total: 45 });
});

test("a segment the cache has not seen refetches the job and never shrinks the page total", () => {
  // the job was fetched before the task was split: the cache knows no segments, the server's progress knows 315 pages
  const qc = new QueryClient();
  qc.setQueryData(qk.job("j1"), { job: { id: "j1" }, tasks: [task({ segments: [], progress: { pages_done: 0, pages_total: 315 } })] });
  const spy = vi.spyOn(qc, "invalidateQueries");
  applyEvent(qc, { kind: "segment.updated", seq: 5, payload: { id: "s0", idx: 0, page_start: 1, page_end: 40, status: "done", task_id: "t1" } } as any);
  const t = (qc.getQueryData(qk.job("j1")) as any).tasks[0];
  expect(t.progress).toEqual({ pages_done: 40, pages_total: 315 });
  expect(spy).toHaveBeenCalledWith({ queryKey: qk.job("j1") });
});

test("an event that arrives while the job is being fetched is not lost to the older snapshot (I1)", async () => {
  const qc = new QueryClient();
  let serverStatus = "queued";
  const fetchJob = async () => {
    const snap = serverStatus; // the server answered before the event happened...
    await new Promise((r) => setTimeout(r, 50)); // ...but the response arrives after it
    return { job: { id: "j1", status: "running", progress: { done: 0, total: 1 } }, tasks: [task({ status: snap })] };
  };
  const unsubscribe = new QueryObserver(qc, { queryKey: qk.job("j1"), queryFn: fetchJob }).subscribe(() => {}); // the page
  await new Promise((r) => setTimeout(r, 10));
  serverStatus = "done";
  applyEvent(qc, { kind: "task.updated", seq: 9, payload: task({ status: "done" }) } as any);
  await new Promise((r) => setTimeout(r, 200));
  unsubscribe();
  expect((qc.getQueryData(qk.job("j1")) as any).tasks[0].status).toBe("done");
});

test("job.updated upserts into the list (newest first) and the detail", () => {
  const qc = new QueryClient();
  qc.setQueryData(qk.jobs(), [{ id: "j0", status: "done" }]);
  qc.setQueryData(qk.job("j1"), { job: { id: "j1", status: "queued" }, tasks: [] });
  applyEvent(qc, { kind: "job.updated", seq: 5, payload: { id: "j1", status: "running", progress: { done: 0, total: 2 } } } as any);
  expect((qc.getQueryData(qk.jobs()) as any).map((j: any) => j.id)).toEqual(["j1", "j0"]);
  expect((qc.getQueryData(qk.job("j1")) as any).job.status).toBe("running");
  applyEvent(qc, { kind: "job.updated", seq: 6, payload: { id: "j1", status: "done", progress: { done: 2, total: 2 } } } as any);
  expect((qc.getQueryData(qk.jobs()) as any)).toHaveLength(2);
});

test("queue.updated merges into system", () => {
  const qc = new QueryClient();
  qc.setQueryData(qk.system(), { gpu: null, queue: { paused: false, length: 0, running_task_id: null } });
  applyEvent(qc, { kind: "queue.updated", seq: 7, payload: { paused: true, length: 3, running_task_id: "t1" } } as any);
  expect((qc.getQueryData(qk.system()) as any).queue).toEqual({ paused: true, length: 3, running_task_id: "t1" });
});

test("resync invalidates jobs, documents and system", () => {
  const qc = new QueryClient();
  const spy = vi.spyOn(qc, "invalidateQueries");
  applyEvent(qc, { kind: "resync", seq: 0, payload: {} } as any);
  expect(spy).toHaveBeenCalledWith({ queryKey: ["jobs"] });
  expect(spy).toHaveBeenCalledWith({ queryKey: ["documents"] });
  expect(spy).toHaveBeenCalledWith({ queryKey: ["system"] });
});

test("resync marks a gap in every log buffer and clears the stale flag", () => {
  const qc = new QueryClient();
  const logs = new LogStore(500);
  logs.append("t1", "a", 1);
  logs.setStale(true);
  applyEvent(qc, { kind: "resync", seq: 0, payload: {} } as any, logs);
  expect(logs.stale).toBe(false);
  expect(logs.get("t1").at(-1)).toMatchObject({ gap: true });
});

test("task.log and setup.log go to the log store", () => {
  const qc = new QueryClient();
  const logs = new LogStore(500);
  applyEvent(qc, { kind: "task.log", seq: 8, payload: { task_id: "t1", line: "page 3/40", ts: 1 } } as any, logs);
  applyEvent(qc, { kind: "setup.log", seq: 9, payload: { engine: "docling", line: "uv sync" } } as any, logs);
  expect(logs.get("t1")[0].line).toBe("page 3/40");
  expect(logs.get("setup:docling")[0].line).toBe("uv sync");
});

test("log store is bounded", () => {
  const s = new LogStore(500);
  for (let i = 0; i < 600; i++) s.append("t1", `line ${i}`, i);
  expect(s.get("t1").length).toBe(500);
  expect(s.get("t1")[0].line).toBe("line 100");
});

test("log lines lose ANSI escapes, keep only the last \r state, and a tqdm bar updates in place (Q2)", () => {
  const s = new LogStore(500);
  s.append("t", "\u001b[32mINFO\u001b[0m model loaded \u001b]0;title\u0007", 1);
  s.append("t", "Downloading:  10%|█         | 1/10\rDownloading:  50%|█████     | 5/10\r", 2);
  s.append("t", "Downloading:  90%|█████████ | 9/10", 3);
  s.append("t", "Predict: 100%|██████████| 3/3 [00:01<00:00]", 4);
  expect(s.get("t").map((l) => l.line)).toEqual([
    "INFO model loaded",
    "Downloading:  90%|█████████ | 9/10",
    "Predict: 100%|██████████| 3/3 [00:01<00:00]",
  ]);
});
