import type { McpScope, McpToken } from "@/api/types";

export const DAY = 86400;
export const EXPIRING_SOON_DAYS = 14;
export const DEFAULT_SCOPES: McpScope[] = ["doc4ai:read"];
export const TTL_CHOICES = [30, 90, 180, 365] as const;

export const SCOPE_META: Record<McpScope, { label: string; risk: "low" | "medium" | "high"; hint: string }> = {
  "doc4ai:read": { label: "讀取", risk: "low", hint: "搜尋、列出、讀取、切段、查工作" },
  "doc4ai:convert": { label: "轉換（上傳）", risk: "medium", hint: "以檔案內容送轉換" },
  "doc4ai:convert:local": { label: "轉換（主機路徑）", risk: "high", hint: "以主機路徑送轉換；只限白名單根目錄" },
  "doc4ai:manage": { label: "管理", risk: "high", hint: "取消工作、重新轉換" },
};

export function expiryState(t: McpToken, nowS = Date.now() / 1000): { label: string; tone: "ok" | "warn" | "danger" | "neutral"; daysLeft: number | null } {
  if (t.status === "revoked") return { label: "已撤銷", tone: "neutral", daysLeft: null };
  if (t.status === "expired") return { label: "已過期", tone: "danger", daysLeft: 0 };
  if (t.expires_at == null) return { label: "永不過期", tone: "warn", daysLeft: null };
  const days = Math.max(0, Math.floor((t.expires_at - nowS) / DAY));
  return { label: `剩 ${days} 天`, tone: days < EXPIRING_SOON_DAYS ? "warn" : "ok", daysLeft: days };
}
