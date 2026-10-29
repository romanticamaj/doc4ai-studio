import { test, expect, type Page } from "@playwright/test";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// spec 2026-10-29 §13: Doc4AI folder → convert → verified copy → fallback after the work copy is gone → a visible
// copy failure → retry → backfill. Runs against the throwaway e2e server only (playwright.config.ts).
const fixtures = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../tests/fixtures");
const E2E = process.env.AIDOC_E2E_DIR as string;
const LIB = path.join(E2E, "lib");
const PDF_NAME = "第一章 測試（草稿）.pdf";
const PDF_STEM = "第一章 測試（草稿）";

const sha256 = (p: string) => crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");

test.describe.serial("Doc4AI folder and source copies", () => {
  const seen: string[] = [];
  let pdfDoc = "";
  let docxDoc = "";

  test.beforeEach(async ({ page, baseURL }) => {
    page.on("request", (r) => seen.push(r.url()));
    expect(baseURL).toBeTruthy();
  });

  async function rootField(page: Page) {
    await page.goto("/settings");
    const input = page.locator("#set-root");
    await expect(input).toBeVisible();
    return input;
  }

  async function convert(page: Page, file: string): Promise<{ jobId: string; docId: string }> {
    await page.goto("/convert");
    await page.setInputFiles('input[type="file"]', file);
    await expect(page.getByRole("button", { name: "開始轉換" })).toBeEnabled({ timeout: 15000 });
    await page.getByRole("button", { name: "開始轉換" }).click();
    await expect(page).toHaveURL(/\/jobs\/[0-9a-f]+/);
    await expect(page.locator('[data-task][data-status="done"]')).toHaveCount(1, { timeout: 30000 });
    const jobId = page.url().split("/").pop() as string;
    const href = await page.getByRole("link", { name: /開啟文件/ }).getAttribute("href");
    return { jobId, docId: href!.split("/").pop() as string };
  }

  test("1. Settings: the Doc4AI folder shows out\\ and source\\ and saves", async ({ page, request }) => {
    const input = await rootField(page);
    await input.fill(LIB);
    await expect(page.getByTestId("root-out")).toHaveText(/\\lib\\out$/);
    await expect(page.getByTestId("root-source")).toHaveText(/\\lib\\source$/);
    await page.getByRole("button", { name: "儲存" }).first().click();
    await expect(page.getByText("已儲存").first()).toBeVisible();
    const sys = await (await request.get("/api/system")).json();
    expect(sys.source_dir).toMatch(/lib\\source$/);
    expect(fs.statSync(path.join(LIB, "out")).isDirectory() && fs.statSync(path.join(LIB, "source")).isDirectory()).toBe(true);
  });

  test("2. an invalid folder is refused before saving", async ({ page }) => {
    const input = await rootField(page);
    await input.fill("relative\\x");
    await expect(page.getByTestId("root-dir-problem")).toHaveText(/必須是本機的絕對路徑/);
    await expect(page.getByRole("button", { name: "儲存" }).first()).toBeDisabled();
    await page.getByRole("button", { name: "還原" }).first().click();
    await expect(input).toHaveValue(LIB);
  });

  test("3. a conversion leaves a verified copy under source\\<stem>\\", async ({ page }) => {
    const src = path.join(E2E, PDF_NAME);
    fs.copyFileSync(path.join(fixtures, "text.pdf"), src);
    const { docId } = await convert(page, src);
    pdfDoc = docId;
    const copy = path.join(LIB, "source", PDF_STEM, PDF_NAME);
    expect(fs.existsSync(copy)).toBe(true);
    expect(sha256(copy)).toBe(sha256(path.join(fixtures, "text.pdf")));
  });

  test("4. with the work copy gone the Document view shows the copy", async ({ page, request }) => {
    const detail = await (await request.get(`/api/documents/${pdfDoc}`)).json();
    const job = await (await request.get("/api/jobs")).json();
    // the task that produced the document: its work dir is data/work/<task id>
    const tasks = (await Promise.all(job.jobs.map(async (j: { id: string }) => (await (await request.get(`/api/jobs/${j.id}`)).json()).tasks))).flat();
    const task = tasks.find((t: { document_id: string | null }) => t.document_id === pdfDoc);
    expect(task).toBeTruthy();
    fs.rmSync(path.join(E2E, "data", "work", task.id), { recursive: true, force: true });
    fs.rmSync(path.join(E2E, PDF_NAME), { force: true });
    expect(detail.source_copy.status).toBe("copied");
    await page.goto(`/documents/${pdfDoc}`);
    await expect(page.locator('[aria-label="原始檔"] canvas').first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("source-copy-label")).toBeVisible();
    const after = await (await request.get(`/api/documents/${pdfDoc}`)).json();
    expect(after.source_kind).toBe("source_copy");
  });

  test("5. a copy failure is visible, never fatal, and 重試備份 fixes it", async ({ page }) => {
    const source = path.join(LIB, "source");
    const bak = path.join(LIB, "source.bak");
    fs.renameSync(source, bak);
    fs.writeFileSync(source, "a file where the folder should be");
    try {
      const { docId } = await convert(page, path.join(fixtures, "sample.docx"));
      docxDoc = docId;
      await page.goto(`/documents/${docxDoc}`);
      await expect(page.getByTestId("source-copy-warning")).toBeVisible();
      await expect(page.getByTestId("source-copy-warning")).toContainText("原始檔未備份：寫入失敗");
      await page.goto("/library");
      await expect(page.locator('[data-badge="source_not_copied"]').first()).toHaveText("未備份");
      await expect(page.getByTestId("source-copy-banner")).toContainText("份文件的原始檔沒有備份");
    } finally {
      fs.rmSync(source, { force: true });
      fs.renameSync(bak, source);
    }
    await page.goto(`/documents/${docxDoc}`);
    await page.getByRole("button", { name: /重試備份/ }).click();
    await expect(page.getByTestId("source-copy-warning")).toHaveCount(0);
    expect(fs.existsSync(path.join(source, "sample", "sample.docx"))).toBe(true);
  });

  test("6. backfill: 檢查（不寫入） lists 已存在, 補齊備份 counts every document", async ({ page, request }) => {
    await page.goto("/settings#source-copies");
    await page.getByRole("button", { name: "檢查（不寫入）" }).click();
    const report = page.getByTestId("backfill-report");
    await expect(report).toBeVisible({ timeout: 30000 });
    expect(await report.getByRole("cell", { name: "已存在", exact: true }).count()).toBeGreaterThanOrEqual(2);
    await page.getByRole("button", { name: "補齊備份" }).click();
    await expect(report).toContainText("補齊結果");
    const s = await (await request.get("/api/sources")).json();
    expect(s.copied).toBeGreaterThanOrEqual(2);
    await expect(page.getByTestId("source-copy-stats")).toContainText(`已備份 ${s.copied} 份`);
  });

  test("7. every request went to the e2e server", async ({ baseURL }) => {
    const foreign = seen.filter((u) => !u.startsWith(baseURL as string) && !u.startsWith("data:") && !u.startsWith("blob:"));
    expect(foreign).toEqual([]);
    expect(seen.some((u) => /:(3333|8765)\//.test(u))).toBe(false);
  });
});
