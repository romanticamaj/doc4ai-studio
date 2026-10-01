import { expect, test } from "vitest";
import { expiryState, SCOPE_META } from "@/features/mcp/tokenStatus";
import type { McpToken } from "@/api/types";

const base: McpToken = { id: "t", name: "n", prefix: "doc4ai_pat_AAAA", scopes: ["doc4ai:read"], note: null, created_at: 0, expires_at: null,
  revoked_at: null, revoked_reason: null, rotated_from: null, rate_limit_per_min: null, last_used_at: null, last_used_ip: null, last_client: null,
  status: "active", calls_24h: 0, errors_24h: 0 };
const DAY = 86400;

test("expiry states", () => {
  expect(expiryState({ ...base, status: "revoked", revoked_at: 1 }, 100)).toMatchObject({ label: "已撤銷", tone: "neutral" });
  expect(expiryState({ ...base, status: "expired", expires_at: 50 }, 100)).toMatchObject({ label: "已過期", tone: "danger" });
  expect(expiryState(base, 100)).toMatchObject({ label: "永不過期", tone: "warn", daysLeft: null });
  expect(expiryState({ ...base, expires_at: 100 + 5 * DAY }, 100)).toMatchObject({ label: "剩 5 天", tone: "warn", daysLeft: 5 });
  expect(expiryState({ ...base, expires_at: 100 + 60 * DAY }, 100)).toMatchObject({ label: "剩 60 天", tone: "ok", daysLeft: 60 });
});

test("scope metadata marks the risky scopes", () => {
  expect(SCOPE_META["doc4ai:read"].risk).toBe("low");
  expect(SCOPE_META["doc4ai:convert:local"].risk).toBe("high");
  expect(SCOPE_META["doc4ai:manage"].risk).toBe("high");
});
