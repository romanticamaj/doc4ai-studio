import type { McpCall, McpCallsPage } from "@/api/types";
import type { CallFilters } from "@/api/mcp";

export function matchesFilters(c: McpCall, f: CallFilters): boolean {
  if (f.token_id && c.token_id !== f.token_id) return false;
  if (f.client_id && c.client_id !== f.client_id) return false;
  if (f.tool && c.tool_name !== f.tool) return false;
  if (f.status && c.status !== f.status) return false;
  return true;
}

/** Pure: a live `mcp.call` row. A known id is updated in place (the event lacks fields such as `args_summary`,
 *  which the fetched row keeps); a new id goes to the top of page 0 when it matches the filters. Total rows ≤ cap. */
export function mergeLiveCall(pages: McpCallsPage[] | undefined, call: McpCall, f: CallFilters, cap = 500): McpCallsPage[] | undefined {
  if (!pages) return pages;
  if (pages.some((p) => p.calls.some((c) => c.id === call.id)))
    return pages.map((p) => ({ ...p, calls: p.calls.map((c) => (c.id === call.id ? { ...c, ...call } : c)) }));
  if (!matchesFilters(call, f)) return pages;
  const first = pages[0] ?? { calls: [], next_cursor: null };
  const out = [{ ...first, calls: [call, ...first.calls] }, ...pages.slice(1)];
  let total = out.reduce((n, p) => n + p.calls.length, 0);
  for (let i = out.length - 1; i >= 0 && total > cap; i--) {
    const drop = Math.min(total - cap, out[i].calls.length);
    out[i] = { ...out[i], calls: out[i].calls.slice(0, out[i].calls.length - drop) };
    total -= drop;
  }
  return out;
}
