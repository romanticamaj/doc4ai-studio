import type { Document } from "@/api/types";
import { cn } from "@/lib/utils";
import { docBadges } from "./badges";

/** 頁碼不完整 / N 頁有問題 (spec 2026-10-01 §9.3); shared by the Library and the Document view. */
export function PageBadges({ doc }: { doc: Pick<Document, "flags" | "page_summary"> }) {
  return (
    <>
      {docBadges(doc).map((b) => (
        <span
          key={b.key}
          data-badge={b.key}
          className={cn(
            "inline-flex h-5 shrink-0 items-center rounded-full px-2 text-[11px] font-medium leading-none",
            b.tone === "danger" ? "bg-danger-soft text-danger" : "bg-warn-soft text-warn",
          )}
        >
          {b.label}
        </span>
      ))}
    </>
  );
}
