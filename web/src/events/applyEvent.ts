import type { QueryClient } from "@tanstack/react-query";
import { qk } from "@/api/queries";
import { qk as mcpQk, type CallFilters } from "@/api/mcp";
import type { AidocEvent, Job, JobDetail, McpCall, McpCallsPage, McpClient, McpToken, Segment, SystemInfo, Task } from "@/api/types";
import { mergeLiveCall } from "@/features/mcp/mergeCalls";
import { logStore as defaultLogs, type LogStore } from "./useTaskLogs";

let lastMcpBump = 0; // stats refetch throttle (mcp.call can arrive many times a second)
const countedCalls = new Set<number>();

/** Live 連線 / Tokens numbers: the call's client gets a request and a last-seen time, its token a last use and a
 *  24 h call (and error) more. Exact values return on the next refetch. */
function bumpClientAndToken(qc: QueryClient, call: McpCall): void {
  if (call.client_id) {
    for (const [key, data] of qc.getQueriesData<{ clients: McpClient[] }>({ queryKey: ["mcp", "clients"] })) {
      if (!data?.clients?.some((c) => c.id === call.client_id)) continue;
      qc.setQueryData(key, {
        ...data,
        clients: data.clients.map((c) =>
          c.id === call.client_id
            ? { ...c, request_count: c.request_count + 1, last_seen: Math.max(c.last_seen, call.ts), last_ip: call.ip ?? c.last_ip, active: true }
            : c,
        ),
      });
    }
  }
  if (call.token_id) {
    qc.setQueryData<McpToken[]>(mcpQk.mcp.tokens(), (old) =>
      Array.isArray(old)
        ? old.map((t) =>
            t.id === call.token_id
              ? { ...t, last_used_at: Math.max(t.last_used_at ?? 0, call.ts), last_used_ip: call.ip ?? t.last_used_ip,
                  calls_24h: t.calls_24h + 1, errors_24h: t.errors_24h + (call.status === "ok" ? 0 : 1) }
              : t,
          )
        : old,
    );
  }
}

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
 * A fetch in flight (or not yet answered) started before this event: its snapshot may be older than the event and
 * would overwrite it when it lands. Restart it so the answer includes the event (I1).
 */
function refetchIfInFlight(qc: QueryClient, queryKey: readonly unknown[]): void {
  const st = qc.getQueryState(queryKey);
  if (!st) return;
  if (st.fetchStatus === "fetching" && st.data === undefined) {
    // a first fetch is shared, not restarted, by TanStack: refetch once it has landed
    const cache = qc.getQueryCache();
    const hash = cache.find({ queryKey, exact: true })?.queryHash;
    const off = cache.subscribe((e) => {
      if (e.query.queryHash !== hash || e.type !== "updated") return;
      if (e.action.type === "success" || e.action.type === "error") {
        off();
        void qc.invalidateQueries({ queryKey, exact: true });
      }
    });
    return;
  }
  if (st.fetchStatus === "fetching") void qc.invalidateQueries({ queryKey, exact: true }); // cancels and restarts
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
      refetchIfInFlight(qc, qk.jobs());
      refetchIfInFlight(qc, qk.job(job.id));
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
      refetchIfInFlight(qc, qk.job(task.job_id));
      if (task.status === "done" || task.status === "low") qc.invalidateQueries({ queryKey: ["documents"] });
      return;
    }
    case "segment.updated": {
      const { task_id, ...seg } = ev.payload as Segment & { task_id: string };
      for (const [key, data] of qc.getQueriesData<JobDetail>({ queryKey: ["jobs", "detail"] })) {
        if (!data?.tasks?.some((t) => t.id === task_id)) continue;
        let unknown = false;
        qc.setQueryData<JobDetail>(key, {
          ...data,
          tasks: data.tasks.map((t) => {
            if (t.id !== task_id) return t;
            const segs = (t.segments ?? []).slice();
            const i = segs.findIndex((s) => s.id === seg.id);
            if (i < 0) {
              unknown = true;
              segs.push(seg);
            } else segs[i] = { ...segs[i], ...seg };
            segs.sort((a, b) => a.idx - b.idx);
            // The cache may know only some segments (the job was fetched before the task was split): never let a
            // partial list shrink the server's page total; the refetch below brings the full list.
            const calc = taskProgress(segs);
            const prev = t.progress;
            const partial = prev?.pages_total != null && (calc.pages_total ?? 0) < prev.pages_total;
            const progress = partial
              ? { pages_done: Math.max(calc.pages_done, prev.pages_done), pages_total: prev.pages_total }
              : calc;
            return { ...t, segments: segs, progress };
          }),
        });
        if (unknown) qc.invalidateQueries({ queryKey: key });
        else refetchIfInFlight(qc, key);
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
      qc.invalidateQueries({ queryKey: ["mcp"] });
      logs.markGap();
      logs.setStale(false);
      return;
    case "upload.updated":
      return; // the upload routine tracks its own progress
    case "mcp.call": {
      const call = ev.payload as unknown as McpCall;
      for (const [key, data] of qc.getQueriesData<{ pages: McpCallsPage[]; pageParams: unknown[] }>({ queryKey: ["mcp", "calls"] })) {
        if (!data) continue;
        const filters = (key[2] ?? {}) as CallFilters;
        qc.setQueryData(key, { ...data, pages: mergeLiveCall(data.pages, call, filters) });
      }
      if (Date.now() - lastMcpBump > 5000) {
        lastMcpBump = Date.now();
        qc.invalidateQueries({ queryKey: ["mcp", "stats"] });
      }
      qc.invalidateQueries({ queryKey: mcpQk.mcp.status() });
      if (!countedCalls.has(call.id)) {
        // first sight of this row (an auth-flood aggregate is re-published under the same id): count it once
        countedCalls.add(call.id);
        if (countedCalls.size > 2000) countedCalls.delete(countedCalls.values().next().value as number);
        bumpClientAndToken(qc, call);
      }
      if (call.status === "auth_error") qc.invalidateQueries({ queryKey: mcpQk.mcp.tokens() });
      return;
    }
    case "mcp.client": {
      const { state, ...client } = ev.payload as McpClient & { state: string };
      for (const [key, data] of qc.getQueriesData<{ clients: McpClient[] }>({ queryKey: ["mcp", "clients"] })) {
        if (!data) continue;
        const i = data.clients.findIndex((c) => c.id === client.id);
        const next = i < 0 ? [{ ...client, active: state !== "idle" }, ...data.clients] : data.clients.map((c) => (c.id === client.id ? { ...c, ...client, active: state !== "idle" } : c));
        qc.setQueryData(key, { clients: next });
      }
      qc.invalidateQueries({ queryKey: mcpQk.mcp.status() });
      return;
    }
    case "mcp.token": {
      const t = ev.payload as { id: string; action: string };
      if (t.action === "revoked" || t.action === "rotated") {
        // the old token's clients cannot call any more: out of 活躍 now, not at the next refetch
        for (const [key, data] of qc.getQueriesData<{ clients: McpClient[] }>({ queryKey: ["mcp", "clients"] })) {
          if (!data?.clients?.some((c) => c.token_id === t.id)) continue;
          qc.setQueryData(key, { ...data, clients: data.clients.map((c) => (c.token_id === t.id ? { ...c, token_status: "revoked" as const, active: false } : c)) });
        }
      }
      qc.invalidateQueries({ queryKey: mcpQk.mcp.tokens() });
      qc.invalidateQueries({ queryKey: mcpQk.mcp.status() });
      return;
    }

  }
}
