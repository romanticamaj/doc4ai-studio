import { Check, Minus, TriangleAlert, X as XIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { StepState, TimelineStep } from "./buildTimeline";

const NODE: Record<StepState, string> = {
  done: "border-ok/30 bg-ok-soft text-ok",
  active: "border-primary bg-card text-primary",
  failed: "border-danger/30 bg-danger-soft text-danger",
  warn: "border-warn/35 bg-warn-soft text-warn",
  pending: "border-border bg-card text-muted-foreground",
  skipped: "border-border bg-muted text-muted-foreground",
};

function NodeIcon({ state }: { state: StepState }) {
  const cls = "size-2.5 stroke-[3]";
  if (state === "done") return <Check className={cls} />;
  if (state === "failed") return <XIcon className={cls} />;
  if (state === "warn") return <TriangleAlert className="size-2.5 stroke-[2.5]" />;
  if (state === "skipped") return <Minus className={cls} />;
  if (state === "active") return <span className="size-1.5 animate-pulse rounded-full bg-current" />;
  return null;
}

const ENGINE_RE = /^(使用 )(markitdown|docling|mineru)$/;

export function Timeline({ steps }: { steps: TimelineStep[] }) {
  return (
    <ol className="relative">
      {steps.map((s, i) => {
        const m = ENGINE_RE.exec(s.title);
        return (
          <li key={s.key} className="relative flex gap-3 pb-4 last:pb-0" data-state={s.state}>
            {i < steps.length - 1 && <span className="absolute top-5 bottom-0 left-[9px] w-px bg-border" aria-hidden />}
            <span className={cn("relative z-10 mt-0.5 flex size-[19px] shrink-0 items-center justify-center rounded-full border", NODE[s.state])}>
              <NodeIcon state={s.state} />
            </span>
            <div className="min-w-0 pt-px">
              <p className={cn("text-[13px] font-medium leading-5", s.state === "pending" && "text-muted-foreground")}>
                {m ? (
                  <>
                    {m[1]}
                    <span className="font-mono">{m[2]}</span>
                  </>
                ) : (
                  s.title
                )}
                <span className="sr-only"> ({s.state})</span>
              </p>
              {s.detail && <p className="mt-0.5 text-xs break-words text-muted-foreground">{s.detail}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
