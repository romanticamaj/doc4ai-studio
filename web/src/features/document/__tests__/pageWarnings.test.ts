import { expect, test } from "vitest";
import { pageWarnings } from "../pageWarnings";

test("page warnings from quality.pages", () => {
  const w = pageWarnings({
    score: 1, level: "warn", reasons: [],
    pages: [
      { page: 1, reasons: ["broken_text_layer"], repaired_by: "docling:pypdfium_full_page_ocr" },
      { page: 5, reasons: ["broken_text_layer"] },
      { page: 9, reasons: ["page_map_missing"] },
    ],
  } as never);
  expect(w.get(1)).toEqual({ text: "第 1 頁：文字層損壞，已用 OCR 修復", tone: "info" });
  expect(w.get(5)).toEqual({ text: "第 5 頁：文字層損壞，內容可能是亂碼", tone: "warn" });
  expect(w.get(9)?.tone).toBe("warn");
  expect(pageWarnings(undefined).size).toBe(0);
});

test("dropped and failed pages are named, repaired or not", () => {
  const w = pageWarnings({
    score: 1, level: "warn", reasons: [],
    pages: [
      { page: 2, reasons: ["page_dropped"], repaired_by: "docling:pypdfium_full_page_ocr" },
      { page: 3, reasons: ["page_dropped"] },
      { page: 4, reasons: ["page_conversion_failed"] },
      { page: 6, reasons: ["page_conversion_failed"], repaired_by: "vlm:openai:gemma4:12b" },
    ],
  } as never);
  expect(w.get(2)).toEqual({ text: "第 2 頁：引擎漏掉了整頁文字，已用 OCR 修復", tone: "info" });
  expect(w.get(3)).toEqual({ text: "第 3 頁：引擎漏掉了整頁文字（PDF 這頁有文字，轉出來是空的）", tone: "warn" });
  expect(w.get(4)).toEqual({ text: "第 4 頁：引擎轉換這頁失敗，內容可能缺漏", tone: "warn" });
  expect(w.get(6)).toEqual({ text: "第 6 頁：引擎轉換這頁失敗，已用 VLM OCR 修復（gemma4:12b）", tone: "info" });
});

test("partial, misaligned and oversized pages are named", () => {
  const w = pageWarnings({
    score: 1, level: "warn", reasons: [],
    pages: [
      { page: 2, reasons: ["page_partial"], metrics: { coverage: 0.15 } },
      { page: 3, reasons: ["page_misaligned"], metrics: { found_in: [5] } },
      { page: 5, reasons: ["page_oversized"], metrics: { foreign_pages: [3] } },
      { page: 7, reasons: ["page_partial"], repaired_by: "docling:pypdfium_full_page_ocr" },
    ],
  } as never);
  expect(w.get(2)).toEqual({ text: "第 2 頁：只轉出這頁約 15% 的文字", tone: "warn" });
  expect(w.get(3)).toEqual({ text: "第 3 頁：這頁的文字出現在第 5 頁底下（頁碼對錯）", tone: "warn" });
  expect(w.get(5)).toEqual({ text: "第 5 頁：含有其他頁（第 3 頁）的文字", tone: "warn" });
  expect(w.get(7)).toEqual({ text: "第 7 頁：只轉出部分文字，已用 OCR 修復", tone: "info" });
});
