import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/client";
import { hashFile } from "./hashFile";
import { resumeKeyFor, uploadFile } from "./uploadFile";

export type UploadPhase = "queued" | "hashing" | "uploading" | "done" | "error";
export type UploadItem = {
  key: string;
  file: File;
  phase: UploadPhase;
  /** 0..1 within the current phase */
  progress: number;
  upload_id?: string;
  error?: string;
  errorBody?: Record<string, unknown>;
};

type Deps = { hash?: typeof hashFile; upload?: typeof uploadFile };

let seq = 0;

/**
 * Files run one at a time: hash in the worker (skipped when a saved upload can be resumed), then upload.
 * Hashing everything in parallel would compete for the disk and gain nothing on a local server.
 */
export function useUploads(deps: Deps = {}) {
  const hash = deps.hash ?? hashFile;
  const upload = deps.upload ?? uploadFile;
  const [items, setItems] = useState<UploadItem[]>([]);
  const controllers = useRef(new Map<string, AbortController>());
  const running = useRef<string | null>(null);

  const patch = useCallback((key: string, p: Partial<UploadItem>) => {
    setItems((list) => list.map((it) => (it.key === key ? { ...it, ...p } : it)));
  }, []);

  const run = useCallback(
    async (item: UploadItem) => {
      const ac = new AbortController();
      controllers.current.set(item.key, ac);
      running.current = item.key;
      const f = item.file;
      let resuming = false;
      try {
        resuming = !!sessionStorage.getItem(resumeKeyFor(f));
      } catch {
        /* no storage */
      }
      try {
        const sha = async () => {
          patch(item.key, { phase: "hashing", progress: 0 });
          const s = await hash(f, (d, t) => patch(item.key, { progress: t ? d / t : 1 }), ac.signal);
          patch(item.key, { phase: "uploading", progress: 0 });
          return s;
        };
        patch(item.key, resuming ? { phase: "uploading", progress: 0 } : { phase: "hashing", progress: 0 });
        const id = await upload(f, {
          sha256: sha,
          signal: ac.signal,
          onUploadId: (upload_id) => patch(item.key, { upload_id }),
          onProgress: (sent, total) => patch(item.key, { phase: "uploading", progress: total ? sent / total : 1 }),
        });
        patch(item.key, { phase: "done", progress: 1, upload_id: id, error: undefined });
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        const body = e instanceof ApiError ? e.body : undefined;
        patch(item.key, { phase: "error", error: body?.error ?? (e instanceof Error ? e.message : String(e)), errorBody: body });
      } finally {
        controllers.current.delete(item.key);
        if (running.current === item.key) running.current = null;
        setItems((l) => l.slice()); // let the queue effect pick the next file
      }
    },
    [hash, upload, patch],
  );

  // start the next queued item when nothing runs
  useEffect(() => {
    if (running.current) return;
    const next = items.find((it) => it.phase === "queued");
    if (next) void run(next);
  }, [items, run]);

  const add = useCallback((files: File[] | FileList) => {
    const list = Array.from(files).map<UploadItem>((file) => ({ key: `u${++seq}`, file, phase: "queued", progress: 0 }));
    setItems((cur) => {
      // the same file (name, size, mtime) twice is one input
      const known = new Set(cur.filter((c) => c.phase !== "error").map((c) => resumeKeyFor(c.file)));
      const fresh: UploadItem[] = [];
      for (const it of list) {
        const k = resumeKeyFor(it.file);
        if (known.has(k)) continue; // also within one drop (the same file picked twice, Q5)
        known.add(k);
        fresh.push(it);
      }
      return [...cur, ...fresh];
    });
  }, []);

  const cancel = useCallback((key: string) => {
    controllers.current.get(key)?.abort();
    if (running.current === key) running.current = null;
    setItems((cur) => cur.filter((it) => it.key !== key));
  }, []);

  const retry = useCallback((key: string) => {
    patch(key, { phase: "queued", progress: 0, error: undefined, errorBody: undefined });
  }, [patch]);

  const clear = useCallback(() => {
    controllers.current.forEach((c) => c.abort());
    controllers.current.clear();
    running.current = null;
    setItems([]);
  }, []);

  useEffect(() => () => controllers.current.forEach((c) => c.abort()), []);

  return { items, add, cancel, retry, clear };
}
