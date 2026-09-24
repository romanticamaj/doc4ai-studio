import { useEffect, useRef } from "react";
import { Eraser, WifiOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTaskLogs } from "@/events/useTaskLogs";
import { cn } from "@/lib/utils";

const pad = (n: number) => String(n).padStart(2, "0");
function clock(ts: number): string {
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** Live log for one key (a task id or "setup:<engine>"). Follows new lines unless the user scrolled up. */
export function LogPanel({
  logKey,
  empty = "還沒有 log。轉換開始後會即時出現在這裡（只包含開啟頁面之後的輸出）。",
  className,
  toolbar,
}: {
  logKey: string | undefined;
  empty?: string;
  className?: string;
  toolbar?: React.ReactNode;
}) {
  const { lines, stale, clear } = useTaskLogs(logKey);
  const box = useRef<HTMLDivElement>(null);
  const follow = useRef(true);

  useEffect(() => {
    const el = box.current;
    if (el && follow.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <div className={cn("overflow-hidden rounded-[10px] border bg-card shadow-panel", className)}>
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <span className="text-[13px] font-semibold">即時 log</span>
        {stale && (
          <span className="inline-flex items-center gap-1 text-xs text-warn">
            <WifiOff className="size-3" /> 重新連線中
          </span>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {toolbar}
          <Button variant="ghost" size="xs" onClick={clear} disabled={!lines.length}>
            <Eraser /> 清除
          </Button>
        </div>
      </div>
      <div
        ref={box}
        role="log"
        aria-live="polite"
        tabIndex={0}
        onScroll={(e) => {
          const el = e.currentTarget;
          follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
        }}
        className="h-56 overflow-auto bg-muted/40 px-3 py-2 font-mono text-[12px] leading-[1.6] focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
      >
        {lines.length === 0 ? (
          <p className="py-6 text-center font-sans text-xs text-muted-foreground">{empty}</p>
        ) : (
          lines.map((l, i) => (
            <div key={i} className={cn("flex gap-3 whitespace-pre-wrap break-all", l.gap && "my-1 text-warn")}>
              <span className="shrink-0 text-muted-foreground/70 select-none tabular">{clock(l.ts)}</span>
              <span>{l.line}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
