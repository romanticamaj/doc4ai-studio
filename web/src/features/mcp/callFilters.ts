import type { CallFilters } from "@/api/mcp";

const MAP: Record<string, keyof CallFilters> = { token: "token_id", client: "client_id", tool: "tool", status: "status", since: "since", until: "until" };
export const STATUS_OPTIONS = ["ok", "tool_error", "protocol_error", "auth_error", "forbidden_scope", "rate_limited"] as const;
export const RANGE_OPTIONS = [{ label: "1 小時", s: 3600 }, { label: "24 小時", s: 86400 }, { label: "7 天", s: 604800 }, { label: "全部", s: 0 }] as const;

export function filtersFromParams(p: URLSearchParams): CallFilters {
  const f: CallFilters = {};
  for (const [k, key] of Object.entries(MAP)) {
    const v = p.get(k);
    if (v) f[key] = v;
  }
  return f;
}

export function paramsFromFilters(f: CallFilters, base: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams();
  const tab = base.get("tab");
  if (tab) next.set("tab", tab);
  for (const [k, key] of Object.entries(MAP)) {
    const v = f[key];
    if (v) next.set(k, v);
  }
  return next;
}
