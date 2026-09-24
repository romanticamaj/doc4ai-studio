import type { Chunk } from "@/api/types";

export function parseNdjson(text: string): Chunk[] {
  return text
    .split(/\r?\n/)
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l) as Chunk);
}

export function pageLabel(c: Pick<Chunk, "page_start" | "page_end">): string {
  if (c.page_start == null) return "無頁碼";
  if (c.page_end == null || c.page_end === c.page_start) return `p. ${c.page_start}`;
  return `p. ${c.page_start}–${c.page_end}`;
}
