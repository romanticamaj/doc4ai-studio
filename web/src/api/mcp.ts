import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { McpCallsPage, McpClient, McpSnippet, McpStats, McpStatus, McpToken, McpTokenCreated } from "./types";

export type CallFilters = { token_id?: string; client_id?: string; tool?: string; status?: string; since?: string; until?: string };
export type ClientFilters = { active?: boolean; token_id?: string };

export const qk = {
  mcp: {
    status: () => ["mcp", "status"] as const,
    tokens: () => ["mcp", "tokens"] as const,
    clients: (f: ClientFilters = {}) => ["mcp", "clients", f] as const,
    calls: (f: CallFilters = {}) => ["mcp", "calls", f] as const,
    stats: (w: "24h" | "7d" = "24h") => ["mcp", "stats", w] as const,
    snippets: (endpoint?: string) => ["mcp", "snippets", endpoint ?? ""] as const,
  },
};

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "" && v !== false) u.set(k, String(v === true ? 1 : v));
  const s = u.toString();
  return s ? `?${s}` : "";
}

export const useMcpStatus = () =>
  useQuery({ queryKey: qk.mcp.status(), queryFn: ({ signal }) => api.get<McpStatus>("/api/mcp/status", signal), refetchInterval: 15_000 });
export const useMcpTokens = () =>
  useQuery({ queryKey: qk.mcp.tokens(), queryFn: ({ signal }) => api.get<{ tokens: McpToken[] }>("/api/mcp/tokens", signal).then((r) => r.tokens) });
export const useMcpClients = (f: ClientFilters) =>
  useQuery({ queryKey: qk.mcp.clients(f), queryFn: ({ signal }) => api.get<{ clients: McpClient[] }>(`/api/mcp/clients${qs(f)}`, signal), refetchInterval: 30_000 });
export const useMcpCalls = (f: CallFilters) =>
  useInfiniteQuery({
    queryKey: qk.mcp.calls(f),
    queryFn: ({ pageParam, signal }) => api.get<McpCallsPage>(`/api/mcp/calls${qs({ ...f, cursor: pageParam as string | undefined, limit: 50 })}`, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
export const useMcpStats = (w: "24h" | "7d") =>
  useQuery({ queryKey: qk.mcp.stats(w), queryFn: ({ signal }) => api.get<McpStats>(`/api/mcp/stats?window=${w}`, signal), refetchInterval: 30_000 });
export const useConfigSnippets = (endpoint?: string) =>
  useQuery({
    queryKey: qk.mcp.snippets(endpoint),
    queryFn: ({ signal }) => api.get<{ endpoint_url: string; snippets: McpSnippet[] }>(`/api/mcp/config-snippets${qs({ endpoint })}`, signal),
  });

function useMcpMutation<TVars, TOut>(fn: (v: TVars) => Promise<TOut>) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => qc.invalidateQueries({ queryKey: ["mcp"] }) });
}
export type TokenCreateBody = { name: string; scopes: string[]; expires_in_days?: number; note?: string; rate_limit_per_min?: number };
export const useCreateToken = () => useMcpMutation((b: TokenCreateBody) => api.post<McpTokenCreated>("/api/mcp/tokens", b));
export const usePatchToken = () =>
  useMcpMutation(({ id, ...body }: { id: string; name?: string; note?: string | null; rate_limit_per_min?: number | null }) =>
    api.patch<{ token: McpToken }>(`/api/mcp/tokens/${id}`, body).then((r) => r.token));
export const useRevokeToken = () =>
  useMcpMutation((v: { id: string; reason?: string }) => api.post<{ token: McpToken }>(`/api/mcp/tokens/${v.id}/revoke`, { reason: v.reason }).then((r) => r.token));
export const useRotateToken = () => useMcpMutation((id: string) => api.post<McpTokenCreated>(`/api/mcp/tokens/${id}/rotate`));
