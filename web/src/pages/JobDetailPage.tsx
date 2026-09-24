import { useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { toast } from "sonner";
import { ArrowUpRight, ChevronDown, ChevronLeft, CircleStop, RefreshCw, RotateCcw, Sparkles } from "lucide-react";
import { Page } from "@/components/layout/Page";
import { Panel } from "@/components/Panel";
import { Meter } from "@/components/Meter";
import { StatusBadge, Tag } from "@/components/StatusBadge";
import { ErrorState, EmptyState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useCancelJob, useJob, useRetryTask, useSystem } from "@/api/queries";
import type { Task } from "@/api/types";
import { ApiError } from "@/api/client";
import { buildTimeline, isTerminalJob, jobPercent, percent, progressLabel } from "@/features/jobs/buildTimeline";
import { Timeline } from "@/features/jobs/Timeline";
import { SegmentGrid } from "@/features/jobs/SegmentGrid";
import { LogPanel } from "@/features/jobs/LogPanel";
import { QueueButton } from "@/features/jobs/QueueButton";
import { fileIcon } from "@/features/convert/fileIcon";
import { baseName, formatBytes, formatDateTime, shortId } from "@/lib/format";
import { describeError } from "@/lib/errors";
import { cn } from "@/lib/utils";

const ACTIVE = new Set(["probing", "converting", "checking"]);
const TERMINAL = new Set(["done", "low", "failed", "skipped", "cancelled"]);

export default function JobDetailPage() {
  const { jobId } = useParams();
  const q = useJob(jobId);
  const sys = useSystem();
  const cancel = useCancelJob();
  const [pickedLog, setPickedLog] = useState<string | undefined>();

  const tasks = useMemo(() => q.data?.tasks ?? [], [q.data]);
  const running = tasks.find((t) => t.id === sys.data?.queue.running_task_id) ?? tasks.find((t) => ACTIVE.has(t.status));

  // the log panel follows the running task (else the last active one) until the user picks a file
  const lastActive = useRef<string | undefined>(undefined);
  if (running) lastActive.current = running.id;
  const logTask = pickedLog ?? lastActive.current ?? tasks[0]?.id;
  const setLogTask = setPickedLog;

  if (q.isError) {
    const notFound = q.error instanceof ApiError && q.error.status === 404;
    return (
      <Page>
        <BackLink />
        {notFound ? (
          <EmptyState title="找不到這個工作" description="它可能屬於另一個資料目錄，或已被清除。" action={<Button asChild variant="outline"><Link to="/jobs">回到工作清單</Link></Button>} />
        ) : (
          <ErrorState title="無法載入工作" error={q.error} onRetry={() => q.refetch()} />
        )}
      </Page>
    );
  }
  if (q.isPending) return <DetailSkeleton />;

  const { job } = q.data;
  const title = tasks.length === 1 ? baseName(tasks[0].source_path) : `${tasks.length} 個檔案`;
  const counts = tasks.reduce<Record<string, number>>((m, t) => ((m[t.status] = (m[t.status] ?? 0) + 1), m), {});

  const doCancel = () =>
    cancel.mutateAsync(job.id).then(
      () => toast.success("已取消工作"),
      (e) => toast.error(describeError(e)),
    );

  return (
    <Page>
      <BackLink />
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-xl font-semibold tracking-tight">{title}</h1>
            <StatusBadge status={job.status} />
          </div>
          <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span className="font-mono">{shortId(job.id)}</span>
            <span className="tabular">{formatDateTime(job.created_at)}</span>
            <Tag>{job.origin}</Tag>
            <span>{job.options?.engine ? <>只用 <span className="font-mono">{job.options.engine}</span></> : "自動選擇引擎"}</span>
            <span className="font-mono">{job.options?.lang}</span>
            {job.options?.force && <Tag>force</Tag>}
            {job.options?.retry_low && <Tag>retry-low</Tag>}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <QueueButton />
          <Button variant="outline" size="sm" className="text-danger hover:text-danger" disabled={isTerminalJob(job.status) || cancel.isPending} onClick={doCancel}>
            <CircleStop /> 取消
          </Button>
        </div>
      </div>

      {tasks.length > 1 && (
        <Panel className="mb-5 px-4 py-3.5">
          <div className="flex items-center justify-between gap-4 text-xs">
            <span className="font-medium">
              {job.progress.done}/{job.progress.total} 個檔案已結束
            </span>
            <span className="flex flex-wrap justify-end gap-3 text-muted-foreground">
              {Object.entries(counts).map(([s, n]) => (
                <span key={s} className="font-mono">
                  {s} {n}
                </span>
              ))}
            </span>
          </div>
          <Meter className="mt-2.5" value={jobPercent(job)} tone={job.status === "done" ? "ok" : "info"} label="工作進度" />
        </Panel>
      )}

      <div className="flex flex-col gap-3">
        {tasks.length === 0 ? (
          <EmptyState title="這個工作已沒有檔案" description="它的檔案之後又被轉換過：同一檔案、同一輸出位置只保留最新一次的紀錄，請到 Jobs 看較新的工作。" action={<Button asChild variant="outline"><Link to="/jobs">回到 Jobs</Link></Button>} />
        ) : (
          tasks.map((t) => (
            <TaskCard key={t.id} task={t} defaultOpen={tasks.length <= 3 || ACTIVE.has(t.status) || t.status === "failed"} onShowLog={() => setLogTask(t.id)} />
          ))
        )}
      </div>

      {tasks.length > 0 && (
        <LogPanel
          className="mt-5"
          logKey={logTask}
          toolbar={
            tasks.length > 1 ? (
              <Select value={logTask} onValueChange={setLogTask}>
                <SelectTrigger size="sm" className="h-7 max-w-[220px] text-xs">
                  <SelectValue placeholder="選擇檔案" />
                </SelectTrigger>
                <SelectContent>
                  {tasks.map((t) => (
                    <SelectItem key={t.id} value={t.id}>
                      {baseName(t.source_path)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : undefined
          }
        />
      )}
    </Page>
  );
}

function BackLink() {
  return (
    <Link to="/jobs" className="mb-4 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring">
      <ChevronLeft className="size-3.5" /> Jobs
    </Link>
  );
}

function TaskCard({ task, defaultOpen, onShowLog }: { task: Task; defaultOpen: boolean; onShowLog: () => void }) {
  const [open, setOpen] = useState(defaultOpen);
  const retry = useRetryTask();
  const steps = buildTimeline(task);
  const Icon = fileIcon(task.source_path);
  const active = ACTIVE.has(task.status);
  const terminal = TERMINAL.has(task.status);
  const sourceChanged = (task.error_msg ?? "").startsWith("source_changed");
  const label = progressLabel(task);
  const pct = percent(task);

  const run = (opts: { use_new_version?: boolean; retry_low?: boolean }, done: string) =>
    retry.mutateAsync({ taskId: task.id, ...opts }).then(
      () => toast.success(done),
      (e) => toast.error(describeError(e)),
    );

  return (
    <Panel data-task={task.id} data-status={task.status}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-3 text-left focus-visible:outline-2 focus-visible:outline-ring"
        >
          <span className="flex size-8 shrink-0 items-center justify-center rounded-md border bg-background text-muted-foreground">
            <Icon className="size-4" strokeWidth={1.75} />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-[13px] font-medium">{baseName(task.source_path)}</span>
            <span className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
              <span className="tabular">{formatBytes(task.size)}</span>
              {task.engine && <span className="font-mono">{task.engine}</span>}
              {task.quality && <span className="tabular">品質 {task.quality.score.toFixed(2)}</span>}
            </span>
          </span>
        </button>
        <div className="flex items-center gap-2.5">
          {(active || (task.progress?.pages_total ?? 0) > 1) && label !== "—" && (
            <span className="text-xs text-muted-foreground tabular">{label}</span>
          )}
          <StatusBadge status={task.status} />
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-label={open ? "收合" : "展開"}
            className="flex size-6 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
          >
            <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} />
          </button>
        </div>
      </div>
      {active && (
        <div className="px-4 pb-3">
          <Meter value={pct} indeterminate={label === "—" || pct === 0} label={`${baseName(task.source_path)} 進度`} />
        </div>
      )}
      {open && (
        <div className="grid gap-5 border-t px-4 py-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="min-w-0">
            <Timeline steps={steps} />
          </div>
          <div className="flex min-w-0 flex-col gap-4">
            <SegmentGrid segments={task.segments ?? []} />
            {task.status === "failed" && task.error_msg && (
              <div className="rounded-md border border-danger/20 bg-danger-soft px-3 py-2 text-xs">
                <p className="font-medium text-danger">{task.error_kind ?? "error"}</p>
                <p className="mt-0.5 font-mono break-words text-foreground/80">{task.error_msg}</p>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              {task.document_id && (task.status === "done" || task.status === "low" || task.status === "skipped") && (
                <Button size="sm" asChild>
                  <Link to={`/documents/${task.document_id}`}>
                    開啟文件 <ArrowUpRight />
                  </Link>
                </Button>
              )}
              {task.status === "low" && (
                <Button size="sm" variant="outline" disabled={retry.isPending} onClick={() => run({ retry_low: true }, "已重新排入：重轉低品質")}>
                  <Sparkles /> 重轉低品質
                </Button>
              )}
              {sourceChanged && (
                <Button size="sm" variant="outline" disabled={retry.isPending} onClick={() => run({ use_new_version: true }, "已重新排入：使用新版本")}>
                  <RefreshCw /> 用新版本重轉
                </Button>
              )}
              {terminal && (
                <Button size="sm" variant="outline" disabled={retry.isPending} onClick={() => run({}, "已重新排入")}>
                  <RotateCcw /> 重試
                </Button>
              )}
              <Button size="sm" variant="ghost" className="text-muted-foreground" onClick={onShowLog}>
                看 log
              </Button>
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
}

function DetailSkeleton() {
  return (
    <Page>
      <Skeleton className="mb-5 h-3 w-12" />
      <Skeleton className="h-6 w-64" />
      <Skeleton className="mt-2 h-3 w-80" />
      <div className="mt-7 flex flex-col gap-3">
        {[0, 1].map((i) => (
          <Panel key={i} className="px-4 py-4">
            <div className="flex items-center gap-3">
              <Skeleton className="size-8" />
              <div className="flex-1">
                <Skeleton className="h-3.5 w-48" />
                <Skeleton className="mt-2 h-3 w-24" />
              </div>
              <Skeleton className="h-5 w-16 rounded-full" />
            </div>
          </Panel>
        ))}
      </div>
    </Page>
  );
}
