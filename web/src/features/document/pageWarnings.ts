import type { Quality } from "@/api/types";

export type PageWarning = { text: string; tone: "info" | "warn" };

/** "vlm:openai:qwen2.5-vl-7b" -> "qwen2.5-vl-7b"; "vlm:local:paddleocr-vl-1.6" -> the preset; else null. */
export function vlmModel(repairedBy: string | null | undefined): string | null {
  if (!repairedBy?.startsWith("vlm:")) return null;
  const rest = repairedBy.slice(4);
  const i = rest.indexOf(":");
  return i >= 0 ? rest.slice(i + 1) : rest;
}

function textFor(page: number, reason: string, repaired: boolean, vlm: string | null): PageWarning | null {
  const how = vlm != null ? `已用 VLM OCR 修復（${vlm}）` : "已用 OCR 修復";
  switch (reason) {
    case "broken_text_layer":
      return repaired
        ? { text: `第 ${page} 頁：文字層損壞，${how}`, tone: "info" }
        : { text: `第 ${page} 頁：文字層損壞，內容可能是亂碼`, tone: "warn" };
    case "garbage":
      return repaired
        ? { text: `第 ${page} 頁：亂碼比例過高，${how}`, tone: "info" }
        : { text: `第 ${page} 頁：亂碼比例過高`, tone: "warn" };
    case "page_map_missing":
      return { text: `第 ${page} 頁：缺少頁碼標記`, tone: "warn" };
    case "page_dropped":
      return repaired
        ? { text: `第 ${page} 頁：引擎漏掉了整頁文字，${how}`, tone: "info" }
        : { text: `第 ${page} 頁：引擎漏掉了整頁文字（PDF 這頁有文字，轉出來是空的）`, tone: "warn" };
    case "page_conversion_failed":
      return repaired
        ? { text: `第 ${page} 頁：引擎轉換這頁失敗，${how}`, tone: "info" }
        : { text: `第 ${page} 頁：引擎轉換這頁失敗，內容可能缺漏`, tone: "warn" };
    default:
      return null;
  }
}

/** Per-page notes for the Document view (spec 2026-10-01 §9.4), from the flagged pages in `quality.pages`.
 *  Several reasons on one page: warn wins over info, texts joined with 「；」. */
export function pageWarnings(quality: Quality | undefined | null): Map<number, PageWarning> {
  const out = new Map<number, PageWarning>();
  for (const entry of quality?.pages ?? []) {
    const repaired = !!entry.repaired_by;
    const vlm = vlmModel(entry.repaired_by);
    // advisory-only entries (no reasons, e.g. vlm_low_overlap) produce no note
    const notes = (entry.reasons ?? []).map((r) => textFor(entry.page, r, repaired, vlm)).filter((n): n is PageWarning => !!n);
    if (!notes.length) continue;
    const prev = out.get(entry.page);
    const all = prev ? [prev, ...notes] : notes;
    out.set(entry.page, {
      text: [...new Set(all.map((n) => n.text))].join("；"),
      tone: all.some((n) => n.tone === "warn") ? "warn" : "info",
    });
  }
  return out;
}
