import type { QueryClient } from "@tanstack/react-query";
import { qk } from "@/api/queries";
import type { AidocEvent, Job, JobDetail, Segment, SystemInfo, Task } from "@/api/types";
import { logStore as defaultLogs, type LogStore } from "./useTaskLogs";

const TERMINAL = new Set(["done", "low", "failed", "skipped", "cancelled"]);

/** Same rule as the server's task_progress (serialize.py): pages when segments have ranges, else segment count. */
export function taskProgress(segs: Segment[]): Task["progress"] {
  const ranged = segs.filter((s) => s.page_start != null && s.page_end != null);
  if (ranged.length) {
    const size = (s: Segment) => (s.page_end as number) - (s.page_start as number) + 1;
    return {
      pages_done: ranged.filter((s) => s.status === "done").reduce((a, s) => a + size(s), 0),
      pages_total: ranged.reduce((a, s) => a + size(s), 0),
    };
  }
  return { pages_done: segs.filter((s) => s.status === "done").length, pages_total: segs.length };
}

function withJobProgress(d: JobDetail): JobDetail {
  if (!d.job) return d;
  const done = d.tasks.filter((t) => TERMINAL.has(t.status)).length;
  return { ...d, job: { ...d.job, progress: { done, total: d.tasks.length } } };
}

/**
 * Merge one SSE event into the TanStack Query cache. Pure with respect to the cache: it only reads and writes
 * query data (and the log store for log lines); it never fetches.
 */
export function applyEvent(qc: QueryClient, ev: AidocEvent, logs: LogStore = defaultLogs): void {
  switch (ev.kind) {
    case "job.updated": {
      const job = ev.payload as Job;
      qc.setQueryData<Job[]>(qk.jobs(), (old) => {
        if (!old) return old;
        const i = old.findIndex((j) => j.id === job.id);
        if (i < 0) return [job, ...old];
        const next = old.slice();
        next[i] = { ...old[i], ...job };
        return next;
      });
      qc.setQueryData<JobDetail>(qk.job(job.id), (old) => (old ? { ...old, job: { ...old.job, ...job } } : old));
      return;
    }
    case "task.updated": {
      const task = ev.payload as Task;
      qc.setQueryData<JobDetail>(qk.job(task.job_id), (old) => {
        if (!old) return old;
        const i = old.tasks.findIndex((t) => t.id === task.id);
        const tasks = old.tasks.slice();
        if (i < 0) tasks.push({ segments: [], ...task });
        else tasks[i] = { ...old.tasks[i], ...task, segments: task.segments ?? old.tasks[i].segments };
        return withJobProgress({ ...old, tasks });
      });
      if (task.status === "done" || task.status === "low") qc.invalidateQueries({ queryKey: ["documents"] });
      return;
    }
    case "segment.updated": {
      const { task_id, ...seg } = ev.payload as Segment & { task_id: string };
      for (const [key, data] of qc.getQueriesData<JobDetail>({ queryKey: ["jobs", "detail"] })) {
        if (!data?.tasks?.some((t) => t.id === task_id)) continue;
        qc.setQueryData<JobDetail>(key, {
          ...data,
          tasks: data.tasks.map((t) => {
            if (t.id !== task_id) return t;
            const segs = (t.segments ?? []).slice();
            const i = segs.findIndex((s) => s.id === seg.id);
            if (i < 0) segs.push(seg);
            else segs[i] = { ...segs[i], ...seg };
            segs.sort((a, b) => a.idx - b.idx);
            return { ...t, segments: segs, progress: taskProgress(segs) };
          }),
        });
      }
      return;
    }
    case "task.log": {
      const p = ev.payload as { task_id: string; line: string; ts: number };
      logs.append(p.task_id, p.line, p.ts);
      return;
    }
    case "setup.log": {
      const p = ev.payload as { engine: string; line: string };
      logs.append(`setup:${p.engine}`, p.line, Date.now() / 1000);
      return;
    }
    case "setup.done":
      qc.invalidateQueries({ queryKey: qk.system() });
      return;
    case "queue.updated":
      qc.setQueryData<SystemInfo>(qk.system(), (old) => (old ? { ...old, queue: ev.payload as SystemInfo["queue"] } : old));
      return;
    case "system.updated":
      qc.invalidateQueries({ queryKey: qk.system() });
      if ((ev.payload as Record<string, unknown>)?.settings_changed) qc.invalidateQueries({ queryKey: qk.settings() });
      return;
    case "resync":
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["system"] });
      logs.markGap();
      logs.setStale(false);
      return;
    case "upload.updated":
      return; // the upload routine tracks its own progress
  }
}
