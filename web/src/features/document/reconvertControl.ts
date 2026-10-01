import type { Document } from "@/api/types";
import { needsReconvert } from "@/features/library/badges";

/** 「重新轉換」 in the Document view: only when the document is flagged, and only while a source is left
 *  (otherwise the server can only answer 410 source_missing: show a hint to upload the original again). */
export function reconvertControl(doc: Pick<Document, "flags">, sourceAvailable: boolean): "offer" | "source_gone" | "none" {
  if (!needsReconvert(doc)) return "none";
  return sourceAvailable ? "offer" : "source_gone";
}
