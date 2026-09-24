import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";
import { ChevronDown, Download, FileText, Library, Scissors, TriangleAlert } from "lucide-react";
import { Page, PageHeader } from "@/components/layout/Page";
import { Panel } from "@/components/Panel";
import { EmptyState, ErrorState } from "@/components/states";
import { Tag } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useMutation } from "@tanstack/react-query";
import { useDocuments } from "@/api/queries";
import { api } from "@/api/client";
import type { Chunk } from "@/api/types";
import { pageLabel, parseNdjson } from "@/features/chunks/ndjson";
import { describeError } from "@/lib/errors";
import { cn } from "@/lib/utils";

const PAGE = 100;

type Result = { docId: string; stem: string; maxTokens: number; raw: string; chunks: Chunk[] };

export default function ChunksPage() {
  const [params, setParams] = useSearchParams();
  const docs = useDocuments({});
  const usable = useMemo(() => (docs.data ?? []).filter((d) => d.status !== "orphaned"), [docs.data]);
  const docId = params.get("doc") ?? "";
  const [maxTokens, setMaxTokens] = useState("800");
  const [result, setResult] = useState<Result | null>(null);
  const [shown, setShown] = useState(PAGE);

  const selected = usable.find((d) => d.id === docId);
  const tokens = Number(maxTokens);
  const tokensOk = Number.isInteger(tokens) && tokens >= 1 && tokens <= 100000;

  const preview = useMutation({
    mutationFn: async () => {
      const r = await api.text("/api/chunks", { method: "POST", body: { document_id: docId, max_tokens: tokens } });
      return { docId, stem: selected?.stem ?? "document", maxTokens: tokens, raw: r.text, chunks: parseNdjson(r.text) };
    },
    onSuccess: (r) => {
      setResult(r);
      setShown(PAGE);
    },
    onError: (e) => toast.error(describeError(e)),
  });

  // a new document selection clears a stale result
  useEffect(() => {
    if (result && result.docId !== docId) setResult(null);
  }, [docId, result]);

  const download = () => {
    if (!result) return;
    const url = URL.createObjectURL(new Blob([result.raw], { type: "application/x-ndjson" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${result.stem}.chunks.jsonl`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const oversized = result?.chunks.filter((c) => c.oversized).length ?? 0;

  return (
    <Page>
      <PageHeader title="Chunks" description="把一份文件切成適合 RAG 的段落：依標題分段，保留標題路徑與頁碼範圍。" />

      {docs.isError ? (
        <ErrorState title="無法載入文件清單" error={docs.error} onRetry={() => docs.refetch()} />
      ) : !docs.isPending && usable.length === 0 ? (
        <EmptyState
          icon={Library}
          title="還沒有可以切段的文件"
          description="先轉換一份文件，轉換結果就能在這裡切段。"
          action={
            <Button asChild>
              <Link to="/convert">轉換文件</Link>
            </Button>
          }
        />
      ) : (
        <>
          <Panel className="mb-5">
            <form
              className="flex flex-col gap-4 p-4 md:flex-row md:items-end"
              onSubmit={(e) => {
                e.preventDefault();
                if (docId && tokensOk) preview.mutate();
              }}
            >
              <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                <Label htmlFor="doc">文件</Label>
                {docs.isPending ? (
                  <Skeleton className="h-8 w-full" />
                ) : (
                  <Select value={docId || undefined} onValueChange={(v) => setParams({ doc: v }, { replace: true })}>
                    <SelectTrigger id="doc" className="w-full">
                      <SelectValue placeholder="選擇一份文件" />
                    </SelectTrigger>
                    <SelectContent>
                      {usable.map((d) => (
                        <SelectItem key={d.id} value={d.id}>
                          <span className="truncate">{d.stem}</span>
                          <span className="font-mono text-[11px] text-muted-foreground">{d.engine}</span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              </div>
              <div className="flex flex-col gap-1.5 md:w-36">
                <Label htmlFor="max-tokens">max tokens</Label>
                <Input
                  id="max-tokens"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={100000}
                  value={maxTokens}
                  onChange={(e) => setMaxTokens(e.target.value)}
                  aria-invalid={!tokensOk}
                  className="tabular"
                />
              </div>
              <div className="flex gap-2">
                <Button type="submit" disabled={!docId || !tokensOk || preview.isPending}>
                  <Scissors /> {preview.isPending ? "切段中…" : "預覽"}
                </Button>
                <Button type="button" variant="outline" onClick={download} disabled={!result}>
                  <Download /> 下載 jsonl
                </Button>
              </div>
            </form>
          </Panel>

          {preview.isPending ? (
            <div className="flex flex-col gap-3">
              {[0, 1, 2].map((i) => (
                <Panel key={i} className="p-4">
                  <Skeleton className="h-3 w-56" />
                  <Skeleton className="mt-3 h-3 w-full" />
                  <Skeleton className="mt-2 h-3 w-4/5" />
                </Panel>
              ))}
            </div>
          ) : result ? (
            <>
              <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span>
                  <span className="font-medium text-foreground tabular">{result.chunks.length}</span> 段
                </span>
                <span className="tabular">max tokens {result.maxTokens}</span>
                {oversized > 0 && (
                  <span className="inline-flex items-center gap-1 text-warn">
                    <TriangleAlert className="size-3.5" /> {oversized} 段超過上限（表格或程式碼無法再切）
                  </span>
                )}
              </div>
              {result.chunks.length === 0 ? (
                <EmptyState icon={FileText} title="這份文件沒有可切的內容" description="Markdown 是空的，或只有頁碼標記。" />
              ) : (
                <ol className="flex flex-col gap-2.5">
                  {result.chunks.slice(0, shown).map((c, i) => (
                    <ChunkCard key={c.id} chunk={c} index={i} />
                  ))}
                </ol>
              )}
              {result.chunks.length > shown && (
                <div className="mt-4 text-center">
                  <Button variant="outline" size="sm" onClick={() => setShown((n) => n + PAGE)}>
                    再顯示 {Math.min(PAGE, result.chunks.length - shown)} 段（共 {result.chunks.length}）
                  </Button>
                </div>
              )}
            </>
          ) : (
            <p className="rounded-[10px] border border-dashed px-4 py-10 text-center text-[13px] text-muted-foreground">
              選擇文件並按「預覽」，切段結果會列在這裡。
            </p>
          )}
        </>
      )}
    </Page>
  );
}

function ChunkCard({ chunk, index }: { chunk: Chunk; index: number }) {
  const [open, setOpen] = useState(false);
  const long = chunk.text.length > 420;
  return (
    <li>
      <Panel className={cn("px-4 py-3", chunk.oversized && "border-warn/40")}>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
          <span className="font-mono text-[11px] text-muted-foreground tabular">#{index + 1}</span>
          <nav aria-label="標題路徑" className="min-w-0 flex-1 truncate text-[13px] font-medium">
            {chunk.heading_path.length ? chunk.heading_path.join(" › ") : <span className="text-muted-foreground">（無標題）</span>}
          </nav>
          <Tag mono={false}>{pageLabel(chunk)}</Tag>
          {chunk.oversized && (
            <span className="inline-flex h-5 items-center rounded-full bg-warn-soft px-2 text-[11px] font-medium text-warn">超長</span>
          )}
        </div>
        <p className={cn("mt-2 text-[13px] leading-relaxed whitespace-pre-wrap text-muted-foreground", !open && long && "line-clamp-4")}>
          {chunk.text}
        </p>
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className="truncate font-mono text-[11px] text-muted-foreground/80">{chunk.id}</span>
          {long && (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              className="inline-flex shrink-0 items-center gap-0.5 text-xs text-primary hover:underline focus-visible:outline-2 focus-visible:outline-ring"
            >
              {open ? "收合" : "展開全文"}
              <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
            </button>
          )}
        </div>
      </Panel>
    </li>
  );
}
