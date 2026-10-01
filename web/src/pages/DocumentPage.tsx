import { useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import { ChevronLeft, Copy, Download, FileQuestion, FileWarning, FileX2, FolderX, Link2, Link2Off, RefreshCw, Scissors } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge, Tag } from "@/components/StatusBadge";
import { EmptyState, ErrorState } from "@/components/states";
import { useDocument, useMarkdown, useReconvert } from "@/api/queries";
import { ApiError, withToken } from "@/api/client";
import type { DocumentDetail } from "@/api/types";
import { MarkdownView } from "@/features/document/MarkdownView";
import { PdfViewer } from "@/features/document/PdfViewer";
import { RawSource } from "@/features/document/RawSource";
import { useScrollSync } from "@/features/document/useScrollSync";
import { GoToPage } from "@/features/document/GoToPage";
import { pageWarnings } from "@/features/document/pageWarnings";
import { PageBadges } from "@/features/library/PageBadges";
import { reconvertControl } from "@/features/document/reconvertControl";
import { describeError } from "@/lib/errors";
import { reasonText } from "@/features/jobs/buildTimeline";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { baseName, extOf, formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

const IMAGE_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp"]);

type SourceKind = "pdf" | "image" | "other";
function sourceKind(d: DocumentDetail): SourceKind {
  const ext = extOf(d.sidecar?.source ?? d.document.source_path);
  if (ext === "pdf") return "pdf";
  if (IMAGE_EXT.has(ext)) return "image";
  return "other";
}

export default function DocumentPage() {
  const { docId } = useParams();
  const navigate = useNavigate();
  const q = useDocument(docId);
  const outputOk = q.data?.output_available ?? false;
  const md = useMarkdown(docId, outputOk);
  const [tab, setTab] = useState("markdown");
  const [sync, setSync] = useState(true);
  const [pane, setPane] = useState<"source" | "output">("output");
  const [sourceGone, setSourceGone] = useState(false);
  const wide = useMediaQuery("(min-width: 1024px)");

  const left = useRef<HTMLDivElement>(null);
  const right = useRef<HTMLDivElement>(null);
  const raw = useRef<HTMLDivElement>(null);
  const [pdfPages, setPdfPages] = useState(0);
  const { enabled, page, goTo } = useScrollSync(left, right, {
    active: sync && tab === "markdown" && wide,
    deps: [md.data, pdfPages, tab, wide, pane],
  });
  const quality = q.data?.document.quality;
  const warnings = useMemo(() => pageWarnings(quality), [quality]);
  const warnPages = useMemo(() => new Set(warnings.keys()), [warnings]);
  const reconvert = useReconvert();
  const doReconvert = (id: string) =>
    reconvert.mutateAsync([id]).then(
      ({ jobs }) => {
        if (!jobs.length) {
          toast.error("原始檔與工作副本都不在了，請重新上傳原始檔");
          return;
        }
        toast.success("已排入重新轉換");
        navigate(`/jobs/${jobs[0].id}`);
      },
      (e) =>
        toast.error(e instanceof ApiError && e.status === 410 ? "原始檔與工作副本都不在了，請重新上傳原始檔" : describeError(e)),
    );

  const back = () => (window.history.length > 1 ? navigate(-1) : navigate("/library"));

  if (q.isError) {
    const notFound = q.error instanceof ApiError && q.error.status === 404;
    return (
      <div className="mx-auto max-w-[1200px] px-4 py-8 sm:px-6">
        {notFound ? (
          <EmptyState icon={FileQuestion} title="找不到這份文件" description="它可能已從文件庫清除。" action={<Button asChild variant="outline"><Link to="/library">回到文件庫</Link></Button>} />
        ) : (
          <ErrorState title="無法載入文件" error={q.error} onRetry={() => q.refetch()} />
        )}
      </div>
    );
  }
  if (q.isPending) return <DocSkeleton />;

  const d = q.data;
  const doc = d.document;
  const kind = sourceKind(d);
  const sourceUrl = withToken(`/api/documents/${doc.id}/source?v=${encodeURIComponent(String(doc.created_at))}`);
  const showSource = d.source_available && !sourceGone;

  const copy = async () => {
    if (!md.data) return;
    try {
      await navigator.clipboard.writeText(md.data);
      toast.success("已複製 Markdown");
    } catch {
      toast.error("瀏覽器不允許寫入剪貼簿");
    }
  };

  const sourcePane = (
    <section aria-label="原始檔" className="flex min-h-0 flex-col overflow-hidden rounded-[10px] border bg-muted/50">
      <div className="flex h-10 shrink-0 items-center gap-2 border-b bg-card px-3 text-xs">
        <span className="font-medium">原始檔</span>
        <span className="truncate text-muted-foreground" title={doc.source_path}>
          {baseName(doc.source_path)}
        </span>
        {kind === "pdf" && pdfPages > 0 && <span className="ml-auto shrink-0 text-muted-foreground tabular">{pdfPages} 頁</span>}
      </div>
      {!showSource ? (
        <div className="flex flex-1 items-center justify-center p-6">
          <EmptyState icon={FileX2} className="border-0" title="原始檔已移除" description="原始檔與暫存的工作副本都已不存在。右側的轉換結果不受影響。" />
        </div>
      ) : kind === "pdf" ? (
        <PdfViewer
          ref={left}
          url={sourceUrl}
          className="relative min-h-0 flex-1 overflow-auto"
          onLoaded={setPdfPages}
          warnPages={warnPages}
          onError={(e) => {
            const msg = String((e as { message?: string })?.message ?? "");
            if (/410|Missing|Unexpected server response/i.test(msg)) setSourceGone(true);
            else toast.error("PDF 預覽載入失敗");
          }}
        />
      ) : kind === "image" ? (
        <div ref={left} className="relative min-h-0 flex-1 overflow-auto p-4">
          <img data-page={1} src={sourceUrl} alt={baseName(doc.source_path)} className="mx-auto max-w-full rounded-sm shadow-panel" onError={() => setSourceGone(true)} />
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center p-6">
          <EmptyState
            icon={FileQuestion}
            className="border-0"
            title="這種格式沒有預覽"
            description={`瀏覽器無法直接顯示 .${extOf(doc.source_path) || "?"} 檔。`}
            action={
              <Button variant="outline" size="sm" asChild>
                <a href={sourceUrl} download>
                  <Download /> 下載原始檔
                </a>
              </Button>
            }
          />
        </div>
      )}
    </section>
  );

  const syncButton = (
    <Button
      variant="ghost"
      size="xs"
      disabled={!enabled}
      onClick={() => setSync((s) => !s)}
      aria-pressed={enabled && sync}
      title={enabled ? (sync ? "關閉同步捲動" : "開啟同步捲動") : "這份文件沒有頁碼標記，無法同步捲動"}
      className={cn("font-normal", enabled && sync ? "text-primary" : "text-muted-foreground")}
    >
      {enabled && sync ? <Link2 /> : <Link2Off />}
      {enabled ? (sync ? "同步捲動" : "未同步") : "無頁碼"}
      {enabled && page != null && <span className="ml-0.5 rounded bg-info-soft px-1 font-mono text-[10px] text-primary">p.{page}</span>}
    </Button>
  );

  const outputPane = (
    <section aria-label="轉換結果" className="flex min-h-0 flex-col overflow-hidden rounded-[10px] border bg-card shadow-panel">
      <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col gap-0">
        <div className="flex h-10 shrink-0 items-center gap-2 border-b px-2">
          <TabsList className="h-7">
            <TabsTrigger value="markdown" className="text-xs">Markdown</TabsTrigger>
            <TabsTrigger value="source" className="text-xs">原始碼</TabsTrigger>
            <TabsTrigger value="json" className="text-xs">JSON</TabsTrigger>
          </TabsList>
          <div className="ml-auto">{tab === "markdown" && wide && kind !== "other" && showSource && syncButton}</div>
        </div>
        {!outputOk ? (
          <div className="flex flex-1 items-center justify-center p-6">
            <EmptyState icon={FolderX} className="border-0" title="輸出目錄已不存在（orphaned）" description={`找不到 ${doc.output_dir}。到文件庫按「重新掃描」或清除這筆紀錄。`} />
          </div>
        ) : md.isError ? (
          <div className="p-4">
            <ErrorState title="無法載入 Markdown" error={md.error} onRetry={() => md.refetch()} />
          </div>
        ) : md.isPending ? (
          <div className="space-y-3 p-6">
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-3.5 w-full" />
            <Skeleton className="h-3.5 w-11/12" />
            <Skeleton className="h-3.5 w-4/5" />
          </div>
        ) : (
          <>
            {tab === "markdown" && doc.flags?.includes("page_map_incomplete") && (
              <div role="status" data-testid="page-map-banner" className="flex shrink-0 items-center gap-2 border-b bg-danger-soft px-3 py-2 text-xs">
                <FileWarning className="size-4 shrink-0 text-danger" />
                <span className="flex-1">
                  頁碼不完整（找到 {doc.page_summary?.found ?? 0}／{doc.page_summary?.expected ?? doc.pages ?? "?"} 頁），左右同步可能失準。
                </span>
                {d.source_available ? (
                  <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => doReconvert(doc.id)} disabled={reconvert.isPending}>
                    <RefreshCw /> 重新轉換
                  </Button>
                ) : (
                  <span data-testid="source-gone-hint" className="shrink-0 text-muted-foreground">原始檔不在了，請重新上傳</span>
                )}
              </div>
            )}
            <TabsContent value="markdown" className="relative min-h-0 flex-1 overflow-auto px-6 py-5 sm:px-8" ref={right}>
              <MarkdownView markdown={md.data} docId={doc.id} scrollRef={right} warnings={warnings} version={doc.created_at} />
              {/* scroll past the end: 「跳至頁」 can bring the last pages' anchors to the top of the view */}
              <div aria-hidden className="h-[70svh]" />
            </TabsContent>
            <TabsContent value="source" className="relative min-h-0 flex-1 overflow-auto" ref={raw}>
              <RawSource text={md.data} scrollRef={raw} />
            </TabsContent>
            <TabsContent value="json" className="min-h-0 flex-1 overflow-auto">
              <pre className="min-h-full bg-muted/30 px-5 py-4 font-mono text-[12px] leading-[1.65]">{JSON.stringify(d.sidecar ?? doc, null, 2)}</pre>
            </TabsContent>
          </>
        )}
      </Tabs>
    </section>
  );

  return (
    <div className="flex flex-col px-4 pt-5 pb-4 sm:px-6 lg:h-[calc(100svh-3rem)]">
      <div className="mb-4 flex shrink-0 flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
        <div className="min-w-0">
          <button type="button" onClick={back} className="mb-2 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring">
            <ChevronLeft className="size-3.5" /> Library
          </button>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-xl font-semibold tracking-tight">{doc.stem}</h1>
            <StatusBadge status={doc.status}>
              {doc.status}
              {doc.status !== "orphaned" && <span className="opacity-70">{doc.quality.score.toFixed(2)}</span>}
            </StatusBadge>
            <PageBadges doc={doc} />
          </div>
          <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <Tag>{doc.engine}</Tag>
            <Tag>{doc.lang}</Tag>
            <span className="tabular">{doc.pages ? `${doc.pages} 頁` : "無頁碼"}</span>
            {d.sidecar?.elapsed_s != null && <span className="tabular">轉換 {formatDuration(d.sidecar.elapsed_s)}</span>}
            {doc.quality.reasons.length > 0 && <span className="text-warn">{reasonText(doc.quality.reasons)}</span>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {kind === "pdf" && (doc.pages ?? 0) > 0 && wide && tab === "markdown" && <GoToPage pages={doc.pages ?? 1} onGo={goTo} />}
          {reconvertControl(doc, d.source_available) === "source_gone" && !doc.flags?.includes("page_map_incomplete") && (
            <span data-testid="source-gone-hint" className="text-xs text-muted-foreground">原始檔不在了，請重新上傳後再轉換</span>
          )}
          {reconvertControl(doc, d.source_available) === "offer" && !doc.flags?.includes("page_map_incomplete") && (
            <Button variant="outline" size="sm" onClick={() => doReconvert(doc.id)} disabled={reconvert.isPending}>
              <RefreshCw /> 重新轉換
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={copy} disabled={!md.data}>
            <Copy /> 複製 Markdown
          </Button>
          <Button variant="outline" size="sm" asChild disabled={!outputOk}>
            <a href={withToken(`/api/documents/${doc.id}/download.zip`)} download={`${doc.stem}.zip`} aria-disabled={!outputOk}>
              <Download /> 下載 zip
            </a>
          </Button>
          <Button size="sm" asChild>
            <Link to={`/chunks?doc=${doc.id}`}>
              <Scissors /> 送去切段
            </Link>
          </Button>
        </div>
      </div>

      {wide ? (
        <div className="grid min-h-0 flex-1 grid-cols-2 gap-4">
          {sourcePane}
          {outputPane}
        </div>
      ) : (
        <>
          <div role="radiogroup" aria-label="顯示" className="mb-3 grid grid-cols-2 gap-1 rounded-lg bg-muted p-1">
            {(
              [
                ["source", "原始檔"],
                ["output", "轉換結果"],
              ] as const
            ).map(([v, label]) => (
              <button
                key={v}
                type="button"
                role="radio"
                aria-checked={pane === v}
                onClick={() => setPane(v)}
                className={cn("h-7 rounded-md text-[13px] text-muted-foreground", pane === v && "bg-card font-medium text-foreground shadow-panel")}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="flex h-[75svh] min-h-0 flex-col">{pane === "source" ? sourcePane : outputPane}</div>
        </>
      )}
    </div>
  );
}

function DocSkeleton() {
  return (
    <div className="px-4 pt-5 sm:px-6">
      <Skeleton className="h-3 w-14" />
      <Skeleton className="mt-3 h-6 w-72" />
      <Skeleton className="mt-2 h-3 w-60" />
      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <Skeleton className="h-[70svh] rounded-[10px]" />
        <Skeleton className="h-[70svh] rounded-[10px]" />
      </div>
    </div>
  );
}
