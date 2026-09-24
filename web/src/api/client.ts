import type { ApiErrorBody } from "./types";

const TOKEN_KEY = "aidoc_token";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export class ApiError extends Error {
  status: number;
  body: ApiErrorBody;
  constructor(status: number, body: ApiErrorBody) {
    super(`${body.error} (HTTP ${status})`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const t = getToken();
  return { ...(t ? { Authorization: `Bearer ${t}` } : {}), ...(extra ?? {}) };
}

async function errorBody(res: Response): Promise<ApiErrorBody> {
  try {
    const b = await res.clone().json();
    if (b && typeof b === "object" && typeof b.error === "string") return b as ApiErrorBody;
  } catch {
    /* not JSON */
  }
  return { error: `http_${res.status}` };
}

async function request(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<Response> {
  const init: RequestInit = { method, headers: authHeaders(body !== undefined ? { "Content-Type": "application/json" } : undefined), signal };
  if (body !== undefined) init.body = JSON.stringify(body);
  const res = await fetch(path, init);
  if (!res.ok) throw new ApiError(res.status, await errorBody(res));
  return res;
}

async function json<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const res = await request(method, path, body, signal);
  const text = await res.text();
  return (text ? JSON.parse(text) : {}) as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => json<T>("GET", path, undefined, signal),
  post: <T>(path: string, body?: unknown) => json<T>("POST", path, body ?? {}),
  put: <T>(path: string, body?: unknown) => json<T>("PUT", path, body ?? {}),
  del: <T>(path: string) => json<T>("DELETE", path),
  /** Raw text body (Markdown, NDJSON). */
  text: async (path: string, init?: { method?: string; body?: unknown }) => {
    const res = await request(init?.method ?? "GET", path, init?.body);
    return { text: await res.text(), headers: res.headers };
  },
  /** Upload chunk: returns the raw Response so the caller branches on 409/413/422 itself. Network errors throw. */
  putRaw: (path: string, bytes: Blob | ArrayBuffer | Uint8Array, signal?: AbortSignal) =>
    fetch(path, {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/octet-stream" }),
      body: bytes as BodyInit,
      signal,
    }),
  /** HEAD: throws ApiError on non-2xx (404 = unknown upload). */
  head: async (path: string): Promise<Headers> => {
    const res = await fetch(path, { method: "HEAD", headers: authHeaders() });
    if (!res.ok) throw new ApiError(res.status, { error: res.status === 404 ? "not_found" : `http_${res.status}` });
    return res.headers;
  },
};

export type Api = typeof api;

/** EventSource cannot send headers: the token goes in the query string (the server redacts it from its log). */
export function eventsUrl(): string {
  const t = getToken();
  return t ? `/api/events?token=${encodeURIComponent(t)}` : "/api/events";
}

/** URL for a resource opened by the browser itself (<img>, pdf.js, downloads): same token rule as SSE. */
export function withToken(path: string): string {
  const t = getToken();
  if (!t) return path;
  return `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(t)}`;
}
