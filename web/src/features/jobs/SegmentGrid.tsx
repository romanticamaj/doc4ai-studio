import { Check } from "lucide-react";
import type { Segment } from "@/api/types";
import { cn } from "@/lib/utils";

const TILE: Record<string, string> = {
  done: "border-ok/25 bg-ok-soft text-ok",
  converting: "border-primary/40 bg-info-soft text-primary",
  failed: "border-danger/30 bg-danger-soft text-danger",
  queued: "border-border bg-card text-muted-foreground",
};

/** One tile per 40-page segment; done ticks appear as segments finish. */
export function SegmentGrid({ segments }: { segments: Segment[] }) {
  if (segments.length < 2) return null;
  return (
    <div>
      <p className="mb-2 text-xs font-medium text-muted-foreground">
        段落 · {segments.filter((s) => s.status === "done").length}/{segments.length}
      </p>
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(76px,1fr))] gap-1.5">
        {segments.map((s) => (
          <li
            key={s.id}
            title={`段落 ${s.idx + 1}：${s.status}${s.attempt > 1 ? `（第 ${s.attempt} 次）` : ""}`}
            className={cn("flex h-8 items-center justify-between gap-1 rounded-md border px-2 font-mono text-[11px] tabular", TILE[s.status] ?? TILE.queued)}
          >
            <span className="truncate">
              {s.page_start != null ? `p${s.page_start}–${s.page_end}` : `#${s.idx + 1}`}
            </span>
            {s.status === "done" && <Check className="size-3 shrink-0 stroke-[3]" aria-label="done" />}
            {s.status === "converting" && <span className="size-1.5 shrink-0 animate-pulse rounded-full bg-current" aria-label="converting" />}
          </li>
        ))}
      </ul>
    </div>
  );
}
