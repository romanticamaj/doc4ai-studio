import { Link, useLocation, useNavigate } from "react-router";
import { toast } from "sonner";
import { FileSearch, FolderX, LayoutGrid, Library, Rows3, Search, X } from "lucide-react";
import { Page, PageHeader } from "@/components/layout/Page";
import { Panel } from "@/components/Panel";
import { StatusBadge, Tag } from "@/components/StatusBadge";
import { EmptyState, ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useDeleteOrphaned, useDocuments, useRescan } from "@/api/queries";
import type { Document } from "@/api/types";
import { parseFilters, toQuery, toSearch, type Filters } from "@/features/library/filters";
import { fileIcon } from "@/features/convert/fileIcon";
import { baseName, formatDateTime, formatRelative } from "@/lib/format";
import { describeError } from "@/lib/errors";
import { cn } from "@/lib/utils";

const STATUS_OPTIONS = [
  { value: "", label: "全部" },
  { value: "ok", label: "ok" },
  { value: "low", label: "low" },
  { value: "orphaned", label: "orphaned" },
];

export default function LibraryPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const f = parseFilters(location.search);
  const set = (patch: Partial<Filters>) => navigate({ search: toSearch({ ...f, ...patch }) }, { replace: true });

  const docs = useDocuments(toQuery(f));
  const orphaned = useDocuments({ status: "orphaned" });
  const rescan = useRescan();
  const purge = useDeleteOrphaned();
  const nOrphaned = orphaned.data?.length ?? 0;
  const filtered = !!(f.q || f.engine || f.status);

  const doRescan = () =>
    rescan.mutateAsync().then(
      (r) => toast.success(r.orphaned ? `找到 ${r.orphaned} 筆輸出已不存在的紀錄` : "所有輸出目錄都還在"),
      (e) => toast.error(describeError(e)),
    );
  const doPurge = () =>
    purge.mutateAsync().then(
      (r) => toast.success(`已清除 ${r.deleted} 筆紀錄`),
      (e) => toast.error(describeError(e)),
    );

  return (
    <Page>
      <PageHeader
        title="文件庫"
        description="所有已轉換的文件。點一份文件，左右對照原始檔與 Markdown。"
        actions={
          <Button variant="outline" size="sm" onClick={doRescan} disabled={rescan.isPending}>
            <FileSearch /> 重新掃描
          </Button>
        }
      />

      {nOrphaned > 0 && (
        <div role="status" className="mb-5 flex flex-col gap-3 rounded-[10px] border border-warn/30 bg-warn-soft px-4 py-3 sm:flex-row sm:items-center">
          <FolderX className="size-4 shrink-0 text-warn" />
          <p className="flex-1 text-[13px]">
            有 {nOrphaned} 筆紀錄的輸出目錄已不存在
            <span className="text-muted-foreground">（檔案被移動或刪除）。可以清除這些紀錄，或放回檔案後重新掃描。</span>
          </p>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => set({ status: "orphaned" })}>
              查看
            </Button>
            <Button size="sm" variant="outline" onClick={doPurge} disabled={purge.isPending}>
              清除
            </Button>
          </div>
        </div>
      )}

      <div className="mb-4 flex flex-col gap-2.5 md:flex-row md:items-center">
        <div className="relative md:w-72">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            aria-label="搜尋檔名"
            placeholder="搜尋檔名"
            value={f.q}
            onChange={(e) => set({ q: e.target.value })}
            className="pl-8"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <Select value={f.engine || "all"} onValueChange={(v) => set({ engine: v === "all" ? "" : v })}>
            <SelectTrigger aria-label="引擎" className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部引擎</SelectItem>
              <SelectItem value="markitdown">markitdown</SelectItem>
              <SelectItem value="docling">docling</SelectItem>
              <SelectItem value="mineru">mineru</SelectItem>
            </SelectContent>
          </Select>
          <div role="radiogroup" aria-label="品質" className="flex h-8 items-center gap-0.5 rounded-lg bg-muted p-0.5">
            {STATUS_OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                role="radio"
                aria-checked={f.status === o.value}
                onClick={() => set({ status: o.value })}
                className={cn(
                  "h-7 rounded-md px-2.5 text-xs text-muted-foreground transition-colors focus-visible:outline-2 focus-visible:outline-ring",
                  o.value && "font-mono",
                  f.status === o.value && "bg-card font-medium text-foreground shadow-panel",
                )}
              >
                {o.label}
              </button>
            ))}
          </div>
          {filtered && (
            <Button variant="ghost" size="sm" onClick={() => set({ q: "", engine: "", status: "" })}>
              <X /> 清除篩選
            </Button>
          )}
        </div>
        <div role="radiogroup" aria-label="檢視" className="flex h-8 items-center gap-0.5 rounded-lg bg-muted p-0.5 md:ml-auto">
          {(
            [
              ["cards", LayoutGrid, "卡片"],
              ["table", Rows3, "表格"],
            ] as const
          ).map(([v, Icon, label]) => (
            <button
              key={v}
              type="button"
              role="radio"
              aria-checked={f.view === v}
              aria-label={label}
              title={label}
              onClick={() => set({ view: v })}
              className={cn(
                "flex h-7 w-8 items-center justify-center rounded-md text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring",
                f.view === v && "bg-card text-foreground shadow-panel",
              )}
            >
              <Icon className="size-4" strokeWidth={1.75} />
            </button>
          ))}
        </div>
      </div>

      {docs.isError ? (
        <ErrorState title="無法載入文件庫" error={docs.error} onRetry={() => docs.refetch()} />
      ) : docs.isPending ? (
        <CardSkeletons />
      ) : docs.data.length === 0 ? (
        filtered ? (
          <EmptyState icon={Search} title="沒有符合的文件" description="換個關鍵字，或清除篩選條件。" action={<Button variant="outline" onClick={() => set({ q: "", engine: "", status: "" })}>清除篩選</Button>} />
        ) : (
          <EmptyState
            icon={Library}
            title="文件庫是空的"
            description="轉換完成的文件會出現在這裡。"
            action={
              <Button asChild>
                <Link to="/convert">轉換文件</Link>
              </Button>
            }
          />
        )
      ) : f.view === "table" ? (
        <DocTable docs={docs.data} />
      ) : (
        <ul className={cn("grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4", docs.isPlaceholderData && "opacity-60")}>
          {docs.data.map((d) => (
            <li key={d.id}>
              <DocCard doc={d} />
            </li>
          ))}
        </ul>
      )}
    </Page>
  );
}

function QualityBadge({ doc }: { doc: Document }) {
  return (
    <StatusBadge status={doc.status}>
      {doc.status}
      {doc.status !== "orphaned" && doc.quality?.score != null && <span className="opacity-70">{doc.quality.score.toFixed(2)}</span>}
    </StatusBadge>
  );
}

function DocCard({ doc }: { doc: Document }) {
  const Icon = fileIcon(doc.source_path);
  return (
    <Link
      to={`/documents/${doc.id}`}
      className="group flex h-full flex-col rounded-[10px] border bg-card p-4 shadow-panel transition-colors hover:border-foreground/20 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      <div className="flex items-start gap-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-md border bg-background text-muted-foreground">
          <Icon className="size-4" strokeWidth={1.75} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-semibold group-hover:text-primary">{doc.stem}</p>
          <p className="truncate text-xs text-muted-foreground" title={doc.source_path}>
            {baseName(doc.source_path)}
          </p>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-1.5">
        <QualityBadge doc={doc} />
        <Tag>{doc.engine}</Tag>
        <Tag>{doc.lang}</Tag>
      </div>
      <div className="mt-auto flex items-center justify-between pt-4 text-xs text-muted-foreground">
        <span className="tabular">{doc.pages ? `${doc.pages} 頁` : "無頁碼"}</span>
        <span title={formatDateTime(doc.created_at)}>{formatRelative(doc.created_at)}</span>
      </div>
    </Link>
  );
}

function DocTable({ docs }: { docs: Document[] }) {
  const navigate = useNavigate();
  return (
    <Panel className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-[13px]">
        <thead className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
          <tr>
            <th className="px-4 py-2 font-medium">文件</th>
            <th className="px-4 py-2 font-medium">引擎</th>
            <th className="px-4 py-2 font-medium">品質</th>
            <th className="px-4 py-2 text-right font-medium">頁數</th>
            <th className="px-4 py-2 font-medium">建立時間</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {docs.map((d) => (
            <tr key={d.id} className="cursor-pointer hover:bg-muted/40" onClick={() => navigate(`/documents/${d.id}`)}>
              <td className="max-w-0 px-4 py-2.5">
                <Link to={`/documents/${d.id}`} onClick={(e) => e.stopPropagation()} className="block truncate font-medium hover:text-primary focus-visible:outline-2 focus-visible:outline-ring">
                  {d.stem}
                </Link>
                <span className="block truncate text-xs text-muted-foreground">{baseName(d.source_path)}</span>
              </td>
              <td className="px-4 py-2.5 font-mono text-xs">{d.engine}</td>
              <td className="px-4 py-2.5">
                <QualityBadge doc={d} />
              </td>
              <td className="px-4 py-2.5 text-right tabular">{d.pages ?? "—"}</td>
              <td className="px-4 py-2.5 text-xs whitespace-nowrap text-muted-foreground tabular">{formatDateTime(d.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function CardSkeletons() {
  return (
    <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <li key={i}>
          <Panel className="p-4">
            <div className="flex gap-3">
              <Skeleton className="size-8" />
              <div className="flex-1">
                <Skeleton className="h-3.5 w-3/4" />
                <Skeleton className="mt-2 h-3 w-1/2" />
              </div>
            </div>
            <Skeleton className="mt-5 h-5 w-32" />
            <Skeleton className="mt-5 h-3 w-full" />
          </Panel>
        </li>
      ))}
    </ul>
  );
}
