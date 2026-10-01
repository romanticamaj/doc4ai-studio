import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const port = Number(process.env.AIDOC_E2E_PORT ?? 8781);

function sdkCall(token: string): { code: number; out: string } {
  try {
    const out = execFileSync("uv", ["run", "python", "tests/mcp/sdk_call.py", "--url", `http://127.0.0.1:${port}/mcp`, "--token", token, "--json"],
      { cwd: repo, encoding: "utf-8", timeout: 120_000, env: { ...process.env, PYTHONUTF8: "1" } });
    return { code: 0, out };
  } catch (e) {
    const err = e as { status?: number; stdout?: string; stderr?: string };
    return { code: err.status ?? 1, out: (err.stdout ?? "") + (err.stderr ?? "") };
  }
}

test("create token → SDK call → live connection and log → revoke → 401 logged", async ({ page }) => {
  await page.goto("/mcp-admin?tab=tokens");
  await page.getByRole("button", { name: "建立 token" }).click();
  await page.getByLabel("名稱").fill("e2e laptop");
  await page.getByRole("checkbox", { name: /doc4ai:manage/ }).check();
  await expect(page.getByText(/高風險/)).toBeVisible();
  await page.getByRole("button", { name: "建立" }).click();
  const field = page.getByRole("textbox", { name: /token/i }).or(page.locator("input[readonly][value^='doc4ai_pat_']"));
  await expect(field.first()).toBeVisible();
  const token = await field.first().inputValue();
  expect(token).toMatch(/^doc4ai_pat_[0-9A-Za-z]{43}_[0-9A-Za-z]{6}$/);
  await expect(page.getByText("關閉後無法再次查看這把 token")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Claude Code" })).toBeVisible();
  await page.getByRole("button", { name: "我已複製，關閉" }).click();
  const row = page.getByRole("row", { name: /e2e laptop/ });
  await expect(row).toContainText("active");
  await expect(row).toContainText(token.slice(0, 15));
  await expect(page.locator("body")).not.toContainText(token);              // the plaintext is gone after close

  const first = sdkCall(token);
  expect(first.code, first.out).toBe(0);
  expect(JSON.parse(first.out).tools).toContain("read_document");

  await page.getByRole("tab", { name: "連線" }).click();
  await expect(page.getByRole("article", { name: /doc4ai-sdk-call 1.0/ })).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole("article", { name: /doc4ai-sdk-call 1.0/ })).toContainText("2026-07-28");
  await page.getByRole("tab", { name: "呼叫紀錄" }).click();
  await expect(page.getByRole("row", { name: /tools\/list/ }).first()).toContainText("ok", { timeout: 5000 });

  await page.getByRole("tab", { name: "Tokens" }).click();
  await page.getByRole("row", { name: /e2e laptop/ }).getByRole("button", { name: /動作|更多/ }).click();
  await page.getByRole("menuitem", { name: "撤銷" }).click();
  await page.getByLabel("原因").fill("e2e");
  await page.getByRole("button", { name: "確認撤銷" }).click();
  await expect(page.getByRole("row", { name: /e2e laptop/ })).toContainText("revoked");

  const second = sdkCall(token);
  expect(second.code).toBe(1);
  await page.getByRole("tab", { name: "呼叫紀錄" }).click();
  const authRow = page.getByRole("row", { name: /auth_error/ }).first();
  await expect(authRow).toBeVisible({ timeout: 5000 });
  await expect(authRow).toContainText("revoked");
  await expect(authRow).toContainText(token.slice(0, 15));

  await page.goto("/mcp-admin?tab=tokens");
  await expect(page.getByRole("row", { name: /e2e laptop/ })).toBeVisible();
  expect(await page.locator("body").innerText()).not.toContain(token);
});

test("MCP page works at phone width without horizontal scroll", async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 });
  await page.goto("/mcp-admin");
  await expect(page.getByRole("heading", { name: "MCP" })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
});

test("copy buttons work without navigator.clipboard (plain-HTTP tailnet origin)", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  // http://<tailnet-ip>:3333 is not a secure context, so navigator.clipboard is undefined there; simulate that here
  await page.addInitScript(() => Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true }));
  await page.goto("/mcp-admin");
  expect(await page.evaluate(() => typeof navigator.clipboard)).toBe("undefined");
  const firstEndpoint = (await page.locator("li code, li button[aria-pressed]").first().innerText()).trim();
  await page.getByRole("button", { name: "複製" }).first().click();
  await expect(page.getByText("已複製").first()).toBeVisible();
  const reader = await context.newPage();
  await reader.goto("/mcp-admin");
  expect(await reader.evaluate(() => navigator.clipboard.readText())).toBe(firstEndpoint);
  await reader.close();
});
