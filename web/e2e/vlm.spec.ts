import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// VLM plan Task 11b: Settings → openai backend on a fake loopback endpoint → consent → test connection →
// a file the three fake engines all grade low falls through to vlm (timeline + Library).
const fixtures = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../tests/fixtures");
const scenarioPath = path.join(process.env.AIDOC_E2E_DIR!, "scenario.json");
const vlmBase = `http://127.0.0.1:${process.env.AIDOC_E2E_VLM_PORT ?? 8782}/v1`;

test("VLM: configure, consent, test connection, fall through to vlm", async ({ page }) => {
  const original = fs.readFileSync(scenarioPath, "utf-8");
  try {
    await page.goto("/settings#vlm");
    const section = page.locator("#vlm");
    await section.getByRole("combobox", { name: "後端" }).selectOption("openai");
    await section.getByLabel("base_url").fill(vlmBase);
    await section.getByLabel("model").fill("fake-vlm");
    await section.getByLabel("API key").fill("sk-test");
    await expect(section.getByText(/端點在本機/)).toHaveAttribute("data-tone", "info");
    await section.getByRole("switch", { name: /我了解/ }).click();
    await section.getByRole("button", { name: "儲存" }).click();
    await expect(page.getByText("已儲存").first()).toBeVisible();
    await section.getByRole("button", { name: "測試連線" }).click();
    await expect(page.getByText("VLM 端點測試成功")).toBeVisible({ timeout: 20000 });
    await expect(page.getByText("就緒（本機端點）")).toBeVisible({ timeout: 15000 });

    fs.writeFileSync(
      scenarioPath,
      JSON.stringify({ default: "ok", delay_s: 0.3, rules: ["docling", "mineru", "markitdown"].map((engine) => ({ match: { engine }, behavior: "low" })) }),
    );
    await page.goto("/convert");
    await page.setInputFiles('input[type="file"]', path.join(fixtures, "formula.pdf"));
    await expect(page.getByRole("button", { name: "開始轉換" })).toBeEnabled({ timeout: 15000 });
    await page.getByRole("button", { name: "開始轉換" }).click();
    await expect(page).toHaveURL(/\/jobs\/[0-9a-f]+/);
    await expect(page.locator('[data-task][data-status="done"]')).toHaveCount(1, { timeout: 60000 });
    // formula.pdf routes pdf_math: mineru -> docling -> vlm (always last)
    for (const name of ["使用 mineru", "使用 docling"]) await expect(page.getByText(name).first()).toBeVisible();
    await expect(page.getByText("使用 vlm · openai · fake-vlm")).toBeVisible();
    await page.goto("/library?engine=vlm");
    await expect(page.locator('a[href^="/documents/"]').filter({ hasText: "formula" }).first()).toBeVisible();
  } finally {
    fs.writeFileSync(scenarioPath, original);
  }
});
