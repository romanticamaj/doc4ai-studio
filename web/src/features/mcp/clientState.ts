import type { McpClient } from "@/api/types";

export const ACTIVE_WINDOW_S = 300;

export const isActive = (c: McpClient, nowS: number) => nowS - c.last_seen <= ACTIVE_WINDOW_S;

export function splitClients(clients: McpClient[], nowS: number): { active: McpClient[]; history: McpClient[] } {
  const byRecent = (a: McpClient, b: McpClient) => b.last_seen - a.last_seen;
  return { active: clients.filter((c) => isActive(c, nowS)).sort(byRecent), history: clients.filter((c) => !isActive(c, nowS)).sort(byRecent) };
}

export const clientLabel = (c: McpClient) => (c.client_name === "unknown" ? "unknown" : [c.client_name, c.client_version].filter(Boolean).join(" "));
