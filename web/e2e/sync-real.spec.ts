// Acceptance (real engines; run with: pnpm --dir web e2e:real) — spec 2026-10-01 §10.4.
import { expect, test } from "@playwright/test";
import fs from "node:fs";
import { getDocument } from "pdfjs-dist/legacy/build/pdf.mjs";
import { goTo, pickPages, topPage } from "./syncHelpers.ts";

const base = process.env.AIDOC_E2E_REAL_BASE_URL;
const docId = process.env.AIDOC_E2E_REAL_DOC_ID;
const pdfPath = process.env.AIDOC_E2E_REAL_PDF; // tests/fixtures/manual/ortho_p1-80.pdf
const required = process.env.AIDOC_REQUIRE_MANUAL === "1";

const squash = (s: string) => s.normalize("NFKC").replace(/\u00ad|-\n/g, "").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");

test("real document: 20/20 positions, ≥ 18/20 content probes", async ({ page }) => {
  test.setTimeout(240_000);
  const missing = !base || !docId || !pdfPath || !fs.existsSync(pdfPath);
  if (missing) {
    if (required) throw new Error("AIDOC_E2E_REAL_BASE_URL / AIDOC_E2E_REAL_DOC_ID / AIDOC_E2E_REAL_PDF missing");
    test.skip(true, "real sync sample not configured");
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  const pdf = await getDocument({ data: new Uint8Array(fs.readFileSync(pdfPath!)) }).promise;
  await page.goto(`${base}/documents/${docId}`);
  await expect(page.locator('[aria-label="原始檔"] canvas').first()).toBeVisible({ timeout: 30000 });
  const md = await (await page.request.get(`${base}/api/documents/${docId}/markdown`)).text();
  const sections = new Map<number, string>();
  md.split(/<!-- page: (\d+) -->/).forEach((v, i, a) => { if (i % 2 === 1) sections.set(Number(v), squash(a[i + 1] ?? "")); });
  let pos = 0, content = 0, decidable = 0;
  const misses: string[] = [];
  for (const n of pickPages(pdf.numPages)) {
    await goTo(page, n);
    const l = await topPage(page, "原始檔");
    const r = await topPage(page, "轉換結果");
    if (l === n && r === n) pos++;
    else misses.push(`jump ${n}: left ${l} right ${r}`);
    const text = squash((await (await pdf.getPage(n)).getTextContent()).items.map((it) => ("str" in it ? it.str : "")).join("\n"));
    if (text.length < 120) continue;
    decidable++;
    const probe = text.slice(Math.floor(text.length * 0.4), Math.floor(text.length * 0.4) + 24);
    if (sections.get(n)?.includes(probe)) content++;
    else misses.push(`content ${n}: probe not in its section`);
  }
  console.log(`real sync: positions ${pos}/20, content ${content}/${decidable}`, misses);
  expect(pos).toBe(20);
  expect(content).toBeGreaterThanOrEqual(Math.ceil(decidable * 0.9));
});
