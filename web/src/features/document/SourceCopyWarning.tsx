import { toast } from "sonner";
import { RefreshCw, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useRetrySourceCopy } from "@/api/queries";
import type { SourceCopyDetail } from "@/api/types";
import { describeError } from "@/lib/errors";

/** spec 2026-10-29 §11.2: why a document has no usable source copy, by error code */
const REASONS: Record<string, string> = {
  no_space: "磁碟空間不足",
  unreadable: "無法讀取原始檔",
  sha_mismatch: "內容與轉換時不同",
  collision: "備份位置已被其他檔案佔用",
  io_error: "寫入失敗",
  missing_original: "原始檔不在了",
};

function split(error: string | null | undefined): { code: string; rest: string } {
  const s = (error ?? "").trim();
  const i = s.indexOf(":");
  return i < 0 ? { code: s, rest: "" } : { code: s.slice(0, i).trim(), rest: s.slice(i + 1).trim() };
}

/** "<code>: <detail>" in words, the detail kept (a path or the OS message): the backfill table's 說明 column. */
export function detailText(error: string | null | undefined): string {
  if (!error) return "";
  const { code, rest } = split(error);
  const base = REASONS[code];
  if (!base) return error;
  return rest ? `${base}：${rest}` : base;
}

/** null when the copy is fine. */
export function copyReason(sc: Pick<SourceCopyDetail, "status" | "error" | "present"> | undefined | null): string | null {
  if (!sc) return null;
  if (sc.status === "copied") return sc.present ? null : "備份檔不見了";
  if (sc.status == null) return "尚未補齊備份";
  const code = (sc.error ?? "").split(":", 1)[0].trim();
  return REASONS[code] ?? "寫入失敗";
}

/** The warning above the Document view's source pane, with 「重試備份」. */
export function SourceCopyWarning({ docId, sourceCopy }: { docId: string; sourceCopy: SourceCopyDetail | undefined }) {
  const retry = useRetrySourceCopy(docId);
  const reason = copyReason(sourceCopy);
  if (!reason) return null;
  return (
    <div role="status" data-testid="source-copy-warning" className="flex shrink-0 items-center gap-2 border-b bg-warn-soft px-3 py-2 text-xs">
      <TriangleAlert className="size-4 shrink-0 text-warn" />
      <span className="min-w-0 flex-1 truncate" title={sourceCopy?.error ?? undefined}>
        原始檔未備份：{reason}
        {sourceCopy?.status === "failed" && split(sourceCopy.error).rest ? `（${split(sourceCopy.error).rest}）` : ""}
      </span>
      <Button
        size="sm"
        variant="outline"
        className="h-7 px-2 text-xs"
        disabled={retry.isPending}
        onClick={() => retry.mutateAsync().then(() => toast.success("已備份原始檔"), (e) => toast.error(describeError(e)))}
      >
        <RefreshCw /> 重試備份
      </Button>
    </div>
  );
}
