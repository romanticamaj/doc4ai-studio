import { test, expect } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const fixtures = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../tests/fixtures");

test("upload → convert → preview", async ({ page }) => {
  const csp: string[] = [];
  page.on("console", (m) => {
    if (/Content Security Policy|Refused to|standardFontDataUrl|cMapUrl/i.test(m.text())) csp.push(m.text());
  });
  await page.goto("/convert");
  await page.setInputFiles('input[type="file"]', path.join(fixtures, "text.pdf"));
  await expect(page.getByText("text.pdf")).toBeVisible();
  await expect(page.getByRole("button", { name: "開始轉換" })).toBeEnabled({ timeout: 15000 });
  await page.getByRole("button", { name: "開始轉換" }).click();
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f]+/);
  await expect(page.getByText("使用 docling")).toBeVisible({ timeout: 30000 });
  await expect(page.locator('[data-task][data-status="done"]')).toHaveCount(1, { timeout: 30000 });
  await page.getByRole("link", { name: "Library" }).click();
  await page.locator('a[href^="/documents/"]').filter({ hasText: "text" }).first().click();
  await expect(page.getByRole("tab", { name: "Markdown" })).toBeVisible();
  const output = page.getByRole("region", { name: "轉換結果" });
  await expect(output.locator("[data-page='1']")).toHaveCount(1);
  await expect(output.getByRole("heading", { name: /第 1 頁 Heading 1/ })).toBeVisible();
  await expect(page.locator('[aria-label="原始檔"] canvas').first()).toBeVisible(); // pdf.js worker under the CSP
  expect(csp, "CSP violations or pdf.js font warnings").toEqual([]);
});

test("an HTML source with a script is never rendered on the aidoc origin (C1)", async ({ page, request }) => {
  const html = path.join(fixtures, "..", "..", "web", "e2e", ".data-xss.html");
  const fs = await import("node:fs");
  fs.writeFileSync(
    html,
    '<!doctype html><title>r</title><script>document.title="PWNED"</script><h1>Report with plenty of ordinary words</h1>' +
      '<p>Normal words for the quality gate, long enough to be judged fine.</p><a href="assets/../source">src</a>',
  );
  await page.goto("/convert");
  await page.setInputFiles('input[type="file"]', html);
  await expect(page.getByRole("button", { name: "開始轉換" })).toBeEnabled({ timeout: 15000 });
  await page.getByRole("button", { name: "開始轉換" }).click();
  await expect(page.locator('[data-task][data-status="done"]')).toHaveCount(1, { timeout: 30000 });
  const docHref = await page.getByRole("link", { name: /開啟文件/ }).getAttribute("href");
  const docId = docHref!.split("/").pop();
  const r = await request.get(`/api/documents/${docId}/source`);
  expect(r.headers()["content-type"]).toContain("application/octet-stream");
  expect(r.headers()["content-disposition"]).toMatch(/^attachment/);
  expect(r.headers()["content-security-policy"]).toContain("sandbox");
  await page.goto(`/documents/${docId}`);
  await expect(page.getByRole("tab", { name: "Markdown" })).toBeVisible();
  const hrefs = await page.locator(".md-body a[href]").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
  expect(hrefs.filter((h) => /source|\/api\//.test(h ?? ""))).toEqual([]);
  expect(await page.title()).not.toContain("PWNED");
  fs.unlinkSync(html);
});

test("library filters live in the URL and survive Back", async ({ page }) => {
  await page.goto("/library");
  await page.getByRole("searchbox", { name: "搜尋檔名" }).fill("text");
  await page.getByRole("radio", { name: "表格" }).click();
  await expect(page).toHaveURL(/\?q=text&view=table$/);
  await page.locator('a[href^="/documents/"]').first().click();
  await expect(page).toHaveURL(/\/documents\//);
  await page.goBack();
  await expect(page).toHaveURL(/\?q=text&view=table$/);
  await expect(page.getByRole("searchbox", { name: "搜尋檔名" })).toHaveValue("text");
});

test("dark mode persists across reloads", async ({ page }) => {
  await page.goto("/convert");
  const isDark = () => page.evaluate(() => document.documentElement.classList.contains("dark"));
  const before = await isDark();
  await page.getByRole("button", { name: /theme/i }).click();
  expect(await isDark()).toBe(!before);
  await page.reload();
  expect(await isDark()).toBe(!before);
  await page.getByRole("button", { name: /theme/i }).click();
  await page.reload();
  expect(await isDark()).toBe(before);
});

test("400 px wide: drawer navigation and no horizontal scroll", async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 });
  for (const route of ["/convert", "/jobs", "/library", "/chunks", "/settings"]) {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `horizontal overflow on ${route}`).toBeLessThanOrEqual(0);
  }
  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("link", { name: "Jobs" }).click();
  await expect(page).toHaveURL(/\/jobs$/);
});
