import { Link } from "react-router";
import { KeyRound } from "lucide-react";
import { useSystem } from "@/api/queries";
import { ApiError } from "@/api/client";
import { useEventStream } from "@/events/EventStreamProvider";
import { gb } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Top-bar status: GPU memory, queue length (+ paused), live-update connection. Polls /system every 10 s and is
 *  also updated by queue.updated / system.updated events. */
export function StatusCluster() {
  const sys = useSystem();
  const { connected } = useEventStream();
  const d = sys.data;
  const unauthorized = sys.error instanceof ApiError && sys.error.status === 401;
  const memPct = d?.gpu ? (d.gpu.mem_used / d.gpu.mem_total) * 100 : 0;

  return (
    <div className="flex min-w-0 items-center gap-3 text-xs sm:gap-4">
      {unauthorized && (
        <Link to="/settings" className="inline-flex items-center gap-1.5 rounded-md bg-warn-soft px-2 py-1 font-medium text-warn hover:underline">
          <KeyRound className="size-3.5" /> 需要 API token
        </Link>
      )}
      {d?.gpu && (
        <div className="hidden min-w-0 items-center gap-2.5 md:flex" title={`${d.gpu.name}：顯示記憶體 ${gb(d.gpu.mem_used)} / ${gb(d.gpu.mem_total)} GB`}>
          <span className="truncate text-muted-foreground lg:max-w-none md:max-w-40">{d.gpu.name}</span>
          <div className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-muted" role="meter" aria-label="GPU 記憶體" aria-valuenow={Math.round(memPct)} aria-valuemin={0} aria-valuemax={100}>
            <div className={cn("h-full rounded-full", memPct > 90 ? "bg-warn" : "bg-primary")} style={{ width: `${memPct}%` }} />
          </div>
          <span className="shrink-0 tabular text-muted-foreground">
            {gb(d.gpu.mem_used)} / {gb(d.gpu.mem_total)} GB
          </span>
        </div>
      )}
      {d && (
        <Link to="/jobs" className="inline-flex shrink-0 items-center gap-1.5 rounded-md px-1.5 py-1 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring">
          <span className="tabular">佇列 {d.queue.length}</span>
          {d.queue.paused && <span className="rounded-full bg-warn-soft px-1.5 py-px text-[11px] font-medium text-warn">已暫停</span>}
        </Link>
      )}
      <span
        className="inline-flex shrink-0 items-center gap-1.5 text-muted-foreground"
        title={connected ? "即時更新已連線" : "即時更新重新連線中"}
      >
        <span className={cn("size-2 rounded-full", connected ? "bg-ok" : "animate-pulse bg-muted-foreground/50")} />
        <span className="sr-only sm:not-sr-only">{connected ? "即時" : "重新連線中"}</span>
      </span>
    </div>
  );
}
