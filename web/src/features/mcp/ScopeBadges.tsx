import type { McpScope } from "@/api/types";
import { cn } from "@/lib/utils";
import { SCOPE_META } from "./tokenStatus";

/** Scope pills; the high-risk ones (convert:local, manage) are drawn in the danger tone. */
export function ScopeBadges({ scopes, className }: { scopes: McpScope[]; className?: string }) {
  return (
    <span className={cn("flex flex-wrap gap-1", className)}>
      {scopes.map((s) => {
        const meta = SCOPE_META[s];
        const high = meta?.risk === "high";
        return (
          <span
            key={s}
            title={meta ? `${meta.label}：${meta.hint}` : s}
            className={cn(
              "inline-flex h-5 items-center rounded-md border px-1.5 font-mono text-[11px] leading-none",
              high ? "border-danger/40 bg-danger-soft text-danger" : "text-muted-foreground",
            )}
          >
            {s.replace(/^doc4ai:/, "")}
          </span>
        );
      })}
    </span>
  );
}
