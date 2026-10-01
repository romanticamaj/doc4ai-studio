import type { Quality } from "@/api/types";

export type PageWarning = { text: string; tone: "info" | "warn" };

function textFor(page: number, reason: string, repaired: boolean): PageWarning | null {
  switch (reason) {
    case "broken_text_layer":
      return repaired
        ? { text: `第 ${page} 頁：文字層損壞，已用 OCR 修復`, tone: "info" }
        : { text: `第 ${page} 頁：文字層損壞，內容可能是亂碼`, tone: "warn" };
    case "garbage":
      return repaired
        ? { text: `第 ${page} 頁：亂碼比例過高，已用 OCR 修復`, tone: "info" }
        : { text: `第 ${page} 頁：亂碼比例過高`, tone: "warn" };
    case "page_map_missing":
      return { text: `第 ${page} 頁：缺少頁碼標記`, tone: "warn" };
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
    const notes = entry.reasons.map((r) => textFor(entry.page, r, repaired)).filter((n): n is PageWarning => !!n);
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
