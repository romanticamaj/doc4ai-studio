import type { DocFlag, Document } from "@/api/types";

export type DocBadge = { key: DocFlag; label: string; tone: "danger" | "warn" };

/** Page-quality badges (spec 2026-10-01 §9.3). The rules live on the server: `flags` is shown as given. */
export function docBadges(doc: Pick<Document, "flags" | "page_summary">): DocBadge[] {
  const out: DocBadge[] = [];
  const flags = doc.flags ?? [];
  if (flags.includes("page_map_incomplete")) out.push({ key: "page_map_incomplete", label: "頁碼不完整", tone: "danger" });
  if (flags.includes("page_quality"))
    out.push({ key: "page_quality", label: `${doc.page_summary?.unrepaired ?? 0} 頁有問題`, tone: "warn" });
  return out;
}

/** 「遠端」 for a document a VLM converted on a non-loopback endpoint (VLM spec §4.6 / §10.2); else null. */
export function vlmRemoteNote(doc: {
  engine?: string | null;
  vlm?: { backend?: string | null; local_endpoint?: boolean | null } | null;
}): string | null {
  return doc.engine === "vlm" && doc.vlm?.backend === "openai" && doc.vlm.local_endpoint === false ? "遠端" : null;
}

/** Documents worth the one-click 「重新轉換」. */
export function needsReconvert(doc: Pick<Document, "flags">): boolean {
  return (doc.flags ?? []).some((f) => f === "page_map_incomplete" || f === "page_quality");
}
