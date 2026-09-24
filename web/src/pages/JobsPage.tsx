import { Link, useNavigate } from "react-router";
import { ChevronRight, Layers } from "lucide-react";
import { Page, PageHeader } from "@/components/layout/Page";
import { Panel } from "@/components/Panel";
import { Meter } from "@/components/Meter";
import { StatusBadge, Tag } from "@/components/StatusBadge";
import { EmptyState, ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useJobs } from "@/api/queries";
import type { Job } from "@/api/types";
import { QueueButton } from "@/features/jobs/QueueButton";
import { jobPercent } from "@/features/jobs/buildTimeline";
import { formatDateTime, formatRelative, shortId } from "@/lib/format";

function optionsSummary(job: Job): string {
  const o = job.options;
  if (!o) return "";
  return [o.engine ? `只用 ${o.engine}` : "自動", o.lang, o.force && "force", o.retry_low && "retry-low"].filter(Boolean).join(" · ");
}

export default function JobsPage() {
  const jobs = useJobs();
  const navigate = useNavigate();
  const list = [...(jobs.data ?? [])].sort((a, b) => b.created_at - a.created_at);

  return (
    <Page>
      <PageHeader title="Jobs" description="每次送出的轉換都是一個工作；點進去看每個檔案的進度與 log。" actions={<QueueButton />} />
      {jobs.isError ? (
        <ErrorState title="無法載入工作清單" error={jobs.error} onRetry={() => jobs.refetch()} />
      ) : jobs.isPending ? (
        <Panel className="divide-y">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4 px-4 py-3.5">
              <Skeleton className="h-4 w-20" />
              <Skeleton className="h-4 w-32" />
              <Skeleton className="ml-auto h-1.5 w-40" />
            </div>
          ))}
        </Panel>
      ) : list.length === 0 ? (
        <EmptyState
          icon={Layers}
          title="還沒有工作"
          description="上傳檔案或輸入本機路徑後，轉換工作會出現在這裡。用 aidoc batch 送出的批次也會列出。"
          action={
            <Button asChild>
              <Link to="/convert">轉換文件</Link>
            </Button>
          }
        />
      ) : (
        <Panel className="overflow-hidden">
          <table className="w-full text-[13px]">
            <thead className="hidden border-b bg-muted/40 text-left text-xs text-muted-foreground md:table-header-group">
              <tr>
                <th className="px-4 py-2 font-medium">工作</th>
                <th className="px-4 py-2 font-medium">建立時間</th>
                <th className="px-4 py-2 font-medium">來源</th>
                <th className="px-4 py-2 font-medium">狀態</th>
                <th className="w-[28%] px-4 py-2 font-medium">進度</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody className="divide-y">
              {list.map((job) => (
                <tr
                  key={job.id}
                  onClick={() => navigate(`/jobs/${job.id}`)}
                  className="group grid cursor-pointer grid-cols-[1fr_auto] gap-x-3 gap-y-2 px-4 py-3 hover:bg-muted/40 md:table-row md:p-0"
                >
                  <td className="min-w-0 md:px-4 md:py-3">
                    <Link
                      to={`/jobs/${job.id}`}
                      onClick={(e) => e.stopPropagation()}
                      className="font-mono text-[12.5px] font-medium hover:text-primary focus-visible:outline-2 focus-visible:outline-ring"
                    >
                      {shortId(job.id)}
                    </Link>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">{optionsSummary(job)}</p>
                  </td>
                  <td className="order-3 text-xs text-muted-foreground md:px-4 md:py-3 md:text-[13px] md:text-foreground" title={formatDateTime(job.created_at)}>
                    <span className="md:hidden">{formatRelative(job.created_at)}</span>
                    <span className="hidden tabular md:inline">{formatDateTime(job.created_at)}</span>
                  </td>
                  <td className="order-4 justify-self-end md:px-4 md:py-3">
                    <Tag>{job.origin}</Tag>
                  </td>
                  <td className="order-2 justify-self-end md:px-4 md:py-3">
                    <StatusBadge status={job.status} />
                  </td>
                  <td className="order-5 col-span-2 md:px-4 md:py-3">
                    <div className="flex items-center gap-3">
                      <Meter
                        value={jobPercent(job)}
                        tone={job.status === "done" ? "ok" : job.status === "cancelled" ? "neutral" : "info"}
                        label="進度"
                      />
                      <span className="w-12 shrink-0 text-right text-xs text-muted-foreground tabular">
                        {job.progress?.done ?? 0}/{job.progress?.total ?? 0}
                      </span>
                    </div>
                  </td>
                  <td className="hidden pr-3 text-muted-foreground md:table-cell">
                    <ChevronRight className="size-4 opacity-0 transition-opacity group-hover:opacity-100" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </Page>
  );
}
