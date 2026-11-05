import crypto from "node:crypto";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Local MCP OAuth (spec 2026-11-05 §6, §7.2): the server-rendered 授權連線 page, deny / approve to a loopback callback
// (caught with page.route, no listener needed), the Tokens row and its live revoke.
const CB = "http://127.0.0.1:59123/callback";
const MCP_HEADERS = { "Content-Type": "application/json", Accept: "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28",
  "Mcp-Method": "tools/list" };
const LIST = { jsonrpc: "2.0", id: 1, method: "tools/list", params: { _meta: { "io.modelcontextprotocol/protocolVersion": "2026-07-28",
  "io.modelcontextprotocol/clientCapabilities": {} } } };

function pkce() {
  const verifier = crypto.randomBytes(32).toString("base64url");
  return { verifier, challenge: crypto.createHash("sha256").update(verifier).digest("base64url") };
}

async function register(request: APIRequestContext, name: string): Promise<string> {
  const r = await request.post("/oauth/register", { data: { client_name: name, redirect_uris: ["http://127.0.0.1/callback"], token_endpoint_auth_method: "none" } });
  expect(r.status()).toBe(201);
  return (await r.json()).client_id as string;
}

function authorizeUrl(clientId: string, challenge: string, state: string) {
  const q = new URLSearchParams({ response_type: "code", client_id: clientId, redirect_uri: CB, state, code_challenge: challenge,
    code_challenge_method: "S256", scope: "doc4ai:read doc4ai:convert doc4ai:manage" });
  return `/oauth/authorize?${q}`;
}

/** Catch the loopback callback; resolves with the query it was called with. */
function catchCallback(page: Page): Promise<URLSearchParams> {
  return new Promise((resolve) => {
    void page.route("http://127.0.0.1:59123/**", async (route) => {
      resolve(new URL(route.request().url()).searchParams);
      await route.fulfill({ status: 200, contentType: "text/plain", body: "callback ok" });
    });
  });
}

async function noHorizontalScroll(page: Page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
}

test("consent page: content, phone and desktop width, deny, approve → token → /mcp → Tokens row → live revoke", async ({ page, request }) => {
  const clientId = await register(request, "E2E IDE");

  // ---- the page itself
  const first = pkce();
  await page.setViewportSize({ width: 375, height: 740 });
  const denied = catchCallback(page);
  const res = await page.goto(authorizeUrl(clientId, first.challenge, "deny-state"));
  expect(res?.status()).toBe(200);
  const headers = res!.headers();
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(headers["content-security-policy"]).not.toContain("form-action");
  await expect(page.getByRole("heading", { name: "授權連線" })).toBeVisible();
  await expect(page.getByText("E2E IDE（自報名稱，未驗證）")).toBeVisible();
  await expect(page.getByText(CB)).toBeVisible();
  await expect(page.getByText("回到這台電腦上的程式")).toBeVisible();
  await expect(page.getByText(`本機註冊的 client（${clientId}）`)).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /doc4ai:read/ })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /doc4ai:convert/ })).toBeChecked();
  const manage = page.getByRole("checkbox", { name: /doc4ai:manage/ });
  await expect(manage).not.toBeChecked();
  await expect(page.getByText("高風險")).toBeVisible();
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  await expect(page.getByText("只有在這台電腦的瀏覽器能看到這個頁面。")).toBeVisible();
  expect(await noHorizontalScroll(page)).toBe(true);
  await page.setViewportSize({ width: 1280, height: 800 });
  expect(await noHorizontalScroll(page)).toBe(true);

  // ---- deny: the callback gets access_denied with state and iss
  await page.getByRole("button", { name: "拒絕" }).click();
  const dq = await denied;
  expect(dq.get("error")).toBe("access_denied");
  expect(dq.get("state")).toBe("deny-state");
  expect(dq.get("iss")).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);
  expect(dq.get("code")).toBeNull();
  await page.unrouteAll({ behavior: "ignoreErrors" });

  // ---- approve on a fresh page: a code, exchanged for tokens, which work on /mcp
  const second = pkce();
  const approved = catchCallback(page);
  // a real browser's form POST must carry this page's own Origin (Referrer-Policy no-referrer would make it "null")
  const posted = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith("/oauth/authorize"));
  await page.goto(authorizeUrl(clientId, second.challenge, "ok-state"));
  await page.getByRole("checkbox", { name: /doc4ai:manage/ }).check();
  await page.getByRole("button", { name: "允許連線" }).click();
  const aq = await approved;
  const origin = (await posted).headers()["origin"];
  expect(origin).toBe(dq.get("iss"));
  expect(aq.get("state")).toBe("ok-state");
  expect(aq.get("code")).toMatch(/^doc4ai_ac_/);
  expect(aq.get("iss")).toBe(dq.get("iss"));
  const tok = await request.post("/oauth/token", { form: { grant_type: "authorization_code", code: aq.get("code")!, code_verifier: second.verifier,
    redirect_uri: CB, client_id: clientId } });
  expect(tok.status()).toBe(200);
  const body = await tok.json();
  expect(body.scope).toBe("doc4ai:read doc4ai:convert doc4ai:manage");
  const auth = { ...MCP_HEADERS, Authorization: `Bearer ${body.access_token}` };
  expect((await request.post("/mcp", { data: LIST, headers: auth })).status()).toBe(200);

  // ---- admin: the connection is a Tokens row with the OAuth badge; its menu only revokes; revoke is live
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.goto("/mcp-admin?tab=tokens");
  const row = page.getByRole("row", { name: /E2E IDE/ });
  await expect(row).toContainText("OAuth");
  await expect(row).toContainText("有效");
  await expect(row).toContainText(`來源 ${clientId.slice(0, 14)}`);
  await row.getByRole("button", { name: "動作" }).click();
  await expect(page.getByRole("menuitem")).toHaveText(["撤銷"]);
  await page.getByRole("menuitem", { name: "撤銷" }).click();
  await expect(page.getByText("撤銷後這個用戶端的 access 與 refresh token 立即失效，需要重新授權。")).toBeVisible();
  await page.getByRole("button", { name: "確認撤銷" }).click();
  await expect(row).toContainText("已撤銷");
  const after = await request.post("/mcp", { data: LIST, headers: auth });
  expect(after.status()).toBe(401);
  expect(after.headers()["www-authenticate"]).toContain("resource_metadata=");
});

test("a revoke from elsewhere flips the row without a reload (SSE)", async ({ page, request }) => {
  const clientId = await register(request, "E2E SSE IDE");
  const p = pkce();
  const cb = catchCallback(page);
  await page.goto(authorizeUrl(clientId, p.challenge, "s"));
  await page.getByRole("button", { name: "允許連線" }).click();
  const q = await cb;
  const tok = await (await request.post("/oauth/token", { form: { grant_type: "authorization_code", code: q.get("code")!,
    code_verifier: p.verifier, redirect_uri: CB, client_id: clientId } })).json();
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.goto("/mcp-admin?tab=tokens");
  const row = page.getByRole("row", { name: /E2E SSE IDE/ });
  await expect(row).toContainText("有效");
  expect((await request.post("/oauth/revoke", { form: { token: tok.refresh_token } })).status()).toBe(200);
  await expect(row).toContainText("已撤銷");
});

test("a cross-site POST to the consent endpoint is refused", async ({ request }) => {
  const r = await request.post("/oauth/authorize", { form: { request: "x.y", decision: "allow" }, headers: { Origin: "http://evil.test" } });
  expect(r.status()).toBe(403);
  const s = await request.post("/oauth/authorize", { form: { request: "x.y", decision: "allow" }, headers: { "Sec-Fetch-Site": "cross-site" } });
  expect(s.status()).toBe(403);
});
