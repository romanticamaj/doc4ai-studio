import { api as defaultApi, ApiError, type Api } from "@/api/client";
import type { ApiErrorBody, UploadCreated, UploadPutResult } from "@/api/types";

export const CHUNK_SIZE = 8 * 1024 * 1024;

type UploadApi = Pick<Api, "post" | "head" | "putRaw">;

export type UploadOptions = {
  /** The file's sha256, or a function computing it — only called when a new upload must be created. */
  sha256: string | (() => Promise<string>);
  onProgress?: (sent: number, total: number) => void;
  /** Called once the upload id is known (new or resumed). */
  onUploadId?: (id: string) => void;
  signal?: AbortSignal;
  chunkSize?: number;
  api?: UploadApi;
  resumeKey?: string;
  retryDelayMs?: number;
  maxFailures?: number;
};

export function resumeKeyFor(file: File): string {
  return `aidoc_upload:${file.name}:${file.size}:${file.lastModified}`;
}

function session(): Storage | null {
  try {
    return sessionStorage;
  } catch {
    return null;
  }
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
    const t = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

function isAbort(e: unknown): boolean {
  return e instanceof DOMException && e.name === "AbortError";
}

async function readBody(res: Response): Promise<Record<string, unknown>> {
  try {
    return (await res.json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

/**
 * Resumable chunked upload (spec §8.7): PUT ?offset=received, one chunk at a time.
 * - 409 → continue from the server's `received`.
 * - network error or 5xx → wait, HEAD for Upload-Offset, continue (gives up after `maxFailures` in a row).
 * - 413 / 422 / other 4xx → throw ApiError.
 * The upload id is kept in sessionStorage so a page reload resumes the same upload instead of starting over.
 */
export async function uploadFile(file: File, opts: UploadOptions): Promise<string> {
  const api = opts.api ?? defaultApi;
  const chunk = opts.chunkSize ?? CHUNK_SIZE;
  const key = opts.resumeKey ?? resumeKeyFor(file);
  const retryDelay = opts.retryDelayMs ?? 1000;
  const maxFailures = opts.maxFailures ?? 20;
  const store = session();
  const total = file.size;

  let id: string | null = null;
  let received = 0;

  const saved = store?.getItem(key);
  if (saved) {
    try {
      const h = await api.head(`/api/uploads/${saved}`);
      const length = Number(h.get("Upload-Length"));
      const status = h.get("Upload-Status");
      if ((!length || length === total) && status !== "failed" && status !== "consumed") {
        id = saved;
        received = Number(h.get("Upload-Offset") ?? 0) || 0;
        if (status === "complete") received = total;
      }
    } catch (e) {
      if (isAbort(e)) throw e;
      /* unknown or unreachable: start a new upload */
    }
    if (!id) store?.removeItem(key);
  }

  if (!id) {
    const sha256 = typeof opts.sha256 === "function" ? await opts.sha256() : opts.sha256;
    const created = await api.post<UploadCreated>("/api/uploads", { filename: file.name, size: total, sha256 });
    id = created.upload_id;
    received = created.received ?? 0;
    store?.setItem(key, id);
  }
  opts.onUploadId?.(id);
  opts.onProgress?.(received, total);

  let failures = 0;
  while (received < total || total === 0) {
    if (opts.signal?.aborted) throw opts.signal.reason ?? new DOMException("Aborted", "AbortError");
    const end = Math.min(received + chunk, total);
    let res: Response;
    try {
      res = await api.putRaw(`/api/uploads/${id}?offset=${received}`, file.slice(received, end), opts.signal);
    } catch (e) {
      if (isAbort(e)) throw e;
      received = await recover();
      continue;
    }
    if (res.status === 200) {
      failures = 0;
      const body = (await readBody(res)) as Partial<UploadPutResult>;
      received = typeof body.received === "number" ? body.received : end;
      opts.onProgress?.(received, total);
      if (body.status === "complete" || total === 0) break;
      continue;
    }
    const body = await readBody(res);
    if (res.status === 409 && body.error === "upload_not_receiving") {
      // e.g. the final chunk landed but its response was lost: a complete upload is a success
      if (body.status === "complete" && body.received === total) break;
      store?.removeItem(key);
      throw new ApiError(409, body as ApiErrorBody);
    }
    if (res.status === 409 && typeof body.received === "number") {
      received = body.received;
      opts.onProgress?.(received, total);
      continue;
    }
    if (res.status >= 500 && res.status !== 507) {   // 507 insufficient_disk will not go away by retrying
      received = await recover();
      continue;
    }
    if (res.status === 422 || res.status === 404) {
      store?.removeItem(key); // the server dropped this upload: a retry must start over
    }
    throw new ApiError(res.status, (typeof body.error === "string" ? body : { error: `http_${res.status}` }) as ApiErrorBody);
  }

  store?.removeItem(key);
  return id;

  async function recover(): Promise<number> {
    for (;;) {
      failures += 1;
      if (failures > maxFailures) throw new ApiError(0, { error: "network_error" });
      await sleep(retryDelay * Math.min(failures, 5), opts.signal);
      try {
        const h = await api.head(`/api/uploads/${id}`);
        return Number(h.get("Upload-Offset") ?? received) || 0;
      } catch (e) {
        if (isAbort(e)) throw e;
        if (e instanceof ApiError && e.status === 404) {
          store?.removeItem(key);
          throw e;
        }
      }
    }
  }
}
