import { expect, test } from "vitest";
import { clientLabel, isActive, splitClients } from "@/features/mcp/clientState";
import type { McpClient } from "@/api/types";

const c = (id: string, last_seen: number, extra: Partial<McpClient> = {}): McpClient =>
  ({ id, token_id: "t", token_name: "a", client_name: "claude-code", client_version: "2.3.1", protocol_version: "2026-07-28", user_agent: null,
     first_seen: 0, last_seen, last_ip: "::1", request_count: 1, active: true, ...extra });

test("active window is 300 s from last_seen regardless of the server flag", () => {
  expect(isActive(c("a", 1000, { active: false }), 1200)).toBe(true);
  expect(isActive(c("a", 1000, { active: true }), 1301)).toBe(false);
});

test("split and sort", () => {
  const { active, history } = splitClients([c("old", 100), c("new", 1290), c("mid", 1100)], 1300);
  expect(active.map((x) => x.id)).toEqual(["new", "mid"]);
  expect(history.map((x) => x.id)).toEqual(["old"]);
});

test("labels", () => {
  expect(clientLabel(c("a", 0))).toBe("claude-code 2.3.1");
  expect(clientLabel(c("a", 0, { client_name: "unknown", client_version: "" }))).toBe("unknown");
});
