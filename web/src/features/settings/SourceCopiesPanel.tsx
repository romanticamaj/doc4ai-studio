import { useState } from "react";
import { toast } from "sonner";
import { Archive } from "lucide-react";
import { Panel, PanelHeader } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useBackfill, useBackfillProgress, useSourcesSummary } from "@/api/queries";
import { ApiError } from "@/api/client";
import type { BackfillOutcome, BackfillReport, BackfillRow } from "@/api/types";
import { describeError } from "@/lib/errors";
import { formatBytes } from "@/lib/format";

/** spec 2026-10-29 §11.1 result texts */
export const OUTCOME_TEXT: Record<BackfillOutcome, string> = {
  copied: "已備份",
  already_present: "已存在",
  missing_original: "原始檔不在了",
  sha_mismatch: "原始檔已被修改（內容與轉換時不同）",
  unreadable: "無法讀取（可能被其他程式開著）",
  failed: "失敗",
  skipped_orphaned: "略過（輸出已不存在）",
};
export const FROM_TEXT: Record<NonNullable<BackfillRow["from"]>, string> = {
  work_copy: "工作副本",
  original: "原始檔",
  other_document: "同內容的其他文件",
};

export function outcomeText(row: BackfillRow, dryRun: boolean): string {
  return dryRun && row.outcome === "copied" ? "將備份" : OUTCOME_TEXT[row.outcome];
}

/** Settings → 「原始檔備份」: statistics, 檢查（不寫入） / 補齊備份, the per-document report. */
export function SourceCopiesPanel() {
  const summary = useSourcesSummary();
  const backfill = useBackfill();
  const progress = useBackfillProgress().data;
  const [report, setReport] = useState<BackfillReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const s = summary.data;

  const run = (dryRun: boolean) => {
    setError(null);
    backfill.mutateAsync(dryRun).then(setReport, (e) => {
      const msg = e instanceof ApiError && e.body.error === "backfill_running" ? "已有補齊在進行中" : describeError(e);
      setError(msg);
      toast.error(msg);
    });
  };
  const running = backfill.isPending;
  const live = running && progress && "done" in progress ? progress : null;

  return (
    <Panel>
      <PanelHeader
        title={
          <span className="inline-flex items-center gap-2">
            <Archive className="size-4 text-muted-foreground" strokeWidth={1.75} /> 原始檔備份
          </span>
        }
        description={
          s ? (
            <>
              每份轉換的輸入檔都會在 <span className="font-mono">{s.source_dir}</span> 留一份經 sha256 驗證的備份。
            </>
          ) : undefined
        }
      />
      <div className="flex flex-col gap-3 p-4">
        <p className="text-[13px] tabular" data-testid="source-copy-stats">
          {s
            ? `已備份 ${s.copied} 份／未備份 ${s.not_copied} 份／無法還原 ${s.unrecoverable} 份／檔案遺失 ${s.file_missing} 份`
            : "—"}
          {s && <span className="ml-3 text-xs text-muted-foreground">共 {formatBytes(s.bytes)}，可用 {formatBytes(s.free_bytes)}</span>}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="outline" size="sm" disabled={running} onClick={() => run(true)}>
            檢查（不寫入）
          </Button>
          <Button type="button" size="sm" disabled={running} onClick={() => run(false)}>
            補齊備份
          </Button>
          {running && (
            <span className="text-xs text-muted-foreground tabular" data-testid="backfill-progress">
              {live ? `處理中 ${live.done} / ${live.total}` : "處理中…"}
            </span>
          )}
          {error && (
            <span role="alert" className="text-xs text-danger">
              {error}
            </span>
          )}
        </div>
        {report && (
          <div data-testid="backfill-report">
            <p className="mb-2 text-xs text-muted-foreground">
              {report.dry_run ? "檢查結果（沒有寫入任何檔案）" : "補齊結果"}：
              {(Object.keys(OUTCOME_TEXT) as BackfillOutcome[])
                .filter((k) => report.totals[k])
                .map((k) => `${report.dry_run && k === "copied" ? "將備份" : OUTCOME_TEXT[k]} ${report.totals[k]}`)
                .join("、") || "沒有文件"}
            </p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>文件</TableHead>
                  <TableHead>結果</TableHead>
                  <TableHead>來源</TableHead>
                  <TableHead>說明</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {report.documents.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="max-w-[16rem] truncate" title={r.stem}>
                      {r.stem}
                    </TableCell>
                    <TableCell>{outcomeText(r, report.dry_run)}</TableCell>
                    <TableCell>{r.from ? FROM_TEXT[r.from] : "—"}</TableCell>
                    <TableCell className="max-w-[22rem] truncate font-mono text-xs" title={r.detail ?? r.source ?? ""}>
                      {r.detail ?? ""}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </Panel>
  );
}
