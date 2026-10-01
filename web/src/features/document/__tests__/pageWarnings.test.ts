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
