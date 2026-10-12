import { describe, expect, it } from "vitest";
import { pageWarnings } from "../pageWarnings";

describe("pageWarnings with VLM repairs", () => {
  it("names the VLM model", () => {
    const w = pageWarnings({ score: 1, level: "ok", reasons: [], pages: [{ page: 61, reasons: ["broken_text_layer"], repaired_by: "vlm:openai:qwen2.5-vl-7b" }] } as any);
    expect(w.get(61)).toEqual({ text: "第 61 頁：文字層損壞，已用 VLM OCR 修復（qwen2.5-vl-7b）", tone: "info" });
  });
  it("uses the preset for the local backend", () => {
    const w = pageWarnings({ score: 1, level: "ok", reasons: [], pages: [{ page: 1, reasons: ["garbage"], repaired_by: "vlm:local:paddleocr-vl-1.6" }] } as any);
    expect(w.get(1)?.text).toContain("VLM OCR 修復（paddleocr-vl-1.6）");
  });
});
