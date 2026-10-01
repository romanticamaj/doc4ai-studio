// Left/right page sync accuracy (spec 2026-10-01 §10.4) — fake engines, always runs.
import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { goTo, pickPages, scrollPaneTo, topPage } from "./syncHelpers.ts";

const fixtures = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../tests/fixtures");

test("both panes show the same page: 20 jumps + 10 left scrolls + 10 right scrolls (big.pdf, 45 pages)", async ({ page }) => {
  test.setTimeout(240_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/convert");
  await page.setInputFiles('input[type="file"]', path.join(fixtures, "big.pdf"));
  await expect(page.getByRole("button", { name: "開始轉換" })).toBeEnabled({ timeout: 15000 });
  await page.getByRole("button", { name: "開始轉換" }).click();
  await expect(page.locator('[data-task][data-status="done"]')).toHaveCount(1, { timeout: 60000 });
  await page.getByRole("link", { name: /開啟文件/ }).click();
  await expect(page.locator('[aria-label="原始檔"] canvas').first()).toBeVisible();
  const misses: string[] = [];
  for (const n of pickPages(45)) {
    await goTo(page, n);
    const l = await topPage(page, "原始檔");
    const r = await topPage(page, "轉換結果");
    if (l !== n || r !== n) misses.push(`jump ${n}: left ${l} right ${r}`);
  }
  for (const n of pickPages(45, 10, 7)) {
    await goTo(page, 1);
    await scrollPaneTo(page, "原始檔", n);          // user-style scroll of the left pane only
    const r = await topPage(page, "轉換結果");
    if (r !== n) misses.push(`scroll left ${n}: right ${r}`);
  }
  for (const n of pickPages(45, 10, 11)) {
    await goTo(page, 1);
    await scrollPaneTo(page, "轉換結果", n);        // and the right pane only
    const l = await topPage(page, "原始檔");
    if (l !== n) misses.push(`scroll right ${n}: left ${l}`);
  }
  expect(misses).toEqual([]);
});
