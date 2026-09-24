import { useCallback, useSyncExternalStore } from "react";

export type LogLine = { line: string; ts: number; gap?: boolean };

const EMPTY: LogLine[] = [];

/**
 * Live log lines per task (and per engine setup under "setup:<engine>"), bounded per key.
 * `stale` is true while the event stream is disconnected: lines may be missing until the stream is back.
 */
export class LogStore {
  private buffers = new Map<string, LogLine[]>();
  private subs = new Map<string, Set<() => void>>();
  private staleSubs = new Set<() => void>();
  stale = false;
  private readonly max: number;

  constructor(max = 500) {
    this.max = max;
  }

  append(key: string, line: string, ts: number, gap = false): void {
    const cur = this.buffers.get(key) ?? EMPTY;
    const next = cur.length >= this.max ? cur.slice(cur.length - this.max + 1) : cur.slice();
    next.push(gap ? { line, ts, gap } : { line, ts });
    this.buffers.set(key, next);
    this.subs.get(key)?.forEach((fn) => fn());
  }

  get(key: string): LogLine[] {
    return this.buffers.get(key) ?? EMPTY;
  }

  clear(key: string): void {
    this.buffers.delete(key);
    this.subs.get(key)?.forEach((fn) => fn());
  }

  /** After a resync some lines were never delivered: say so in every open buffer. */
  markGap(): void {
    for (const key of [...this.buffers.keys()]) {
      if (this.get(key).length) this.append(key, "連線中斷期間的 log 未能補回", Date.now() / 1000, true);
    }
  }

  setStale(v: boolean): void {
    if (this.stale === v) return;
    this.stale = v;
    this.staleSubs.forEach((fn) => fn());
  }

  subscribe(key: string, fn: () => void): () => void {
    let set = this.subs.get(key);
    if (!set) this.subs.set(key, (set = new Set()));
    set.add(fn);
    return () => {
      set.delete(fn);
    };
  }

  subscribeStale(fn: () => void): () => void {
    this.staleSubs.add(fn);
    return () => {
      this.staleSubs.delete(fn);
    };
  }
}

export const logStore = new LogStore(500);

export function useTaskLogs(key: string | undefined, store: LogStore = logStore) {
  const k = key ?? "";
  const lines = useSyncExternalStore(
    useCallback((fn: () => void) => store.subscribe(k, fn), [store, k]),
    () => store.get(k),
    () => EMPTY,
  );
  const stale = useSyncExternalStore(
    useCallback((fn: () => void) => store.subscribeStale(fn), [store]),
    () => store.stale,
    () => false,
  );
  const clear = useCallback(() => store.clear(k), [store, k]);
  return { lines, stale, clear };
}
