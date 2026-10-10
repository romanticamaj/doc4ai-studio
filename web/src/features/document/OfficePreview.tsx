import { useEffect, useRef } from "react";
import { Link } from "react-router";
import { Download, FileWarning, Loader2, RefreshCw, Settings as SettingsIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/states";
import { useRequestPreview } from "@/api/queries";
import { withToken } from "@/api/client";
import type { PreviewState } from "@/api/types";

const REASONS: Record<string, string> = {
  timeout: "LibreOffice 轉換逾時",
  conversion_failed: "LibreOffice 無法開啟或轉換這個檔案",
  too_large: "檔案超過預覽大小上限（aidoc.toml [preview] max_mb）",
  source_missing: "原始檔與工作副本都已不存在",
  source_changed: "原始檔已和轉換時不同",
  disabled: "Office 預覽已在設定中關閉（[preview] enabled）",
};

export function previewReason(reason: string): string {
  return REASONS[reason] ?? reason;
}

/**
 * Left pane of the Document view for Word / PowerPoint / Excel / OpenDocument / RTF sources: the server renders
 * them to PDF with LibreOffice on first view (POST …/preview), the SSE event `document.preview` reports the end,
 * and the PDF is shown with the same viewer as PDF sources (`renderPdf`). The original stays downloadable.
 */
export function OfficePreview({
  docId,
  preview,
  version,
  sourceUrl,
  ext,
  renderPdf,
}: {
  docId: string;
  preview: PreviewState;
  version: number | string;
  sourceUrl: string;
  ext: string;
  renderPdf: (url: string) => React.ReactNode;
}) {
  const request = useRequestPreview(docId);
  const asked = useRef(false);
  useEffect(() => {
    if (preview.status === "none" && !asked.current) {
      asked.current = true;
      request.mutate(false);
    }
  }, [preview.status, request]);

  const download = (
    <Button variant="outline" size="sm" asChild>
      <a href={sourceUrl} download>
        <Download /> 下載原始檔
      </a>
    </Button>
  );

  if (preview.status === "ready") {
    return <>{renderPdf(withToken(`/api/documents/${docId}/preview?v=${encodeURIComponent(String(version))}`))}</>;
  }
  if (preview.status === "none" || preview.status === "pending") {
    return (
      <div className="flex flex-1 items-center justify-center p-6" role="status">
        <EmptyState
          icon={Loader2}
          className="border-0 [&_svg]:animate-spin"
          title="正在產生預覽…"
          description={`以 LibreOffice 把 .${ext} 轉成 PDF，第一次開啟需要幾秒到一兩分鐘。`}
        />
      </div>
    );
  }
  if (preview.reason === "soffice_missing") {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <EmptyState
          icon={FileWarning}
          className="border-0"
          title="沒有安裝 LibreOffice，無法預覽 Office 檔"
          description="安裝 LibreOffice 後重新整理這一頁即可看到預覽；右側的轉換結果不受影響。"
          action={
            <div className="flex flex-wrap justify-center gap-2">
              <Button variant="outline" size="sm" asChild>
                <Link to="/settings#office-preview">
                  <SettingsIcon /> 到 Settings 查看
                </Link>
              </Button>
              {download}
            </div>
          }
        />
      </div>
    );
  }
  return (
    <div className="flex flex-1 items-center justify-center p-6">
      <EmptyState
        icon={FileWarning}
        className="border-0"
        title="預覽產生失敗"
        description={previewReason(preview.reason)}
        action={
          <div className="flex flex-wrap justify-center gap-2">
            {preview.reason !== "source_missing" && preview.reason !== "disabled" && (
              <Button variant="outline" size="sm" onClick={() => request.mutate(true)} disabled={request.isPending}>
                <RefreshCw /> 再試一次
              </Button>
            )}
            {preview.reason !== "source_missing" && download}
          </div>
        }
      />
    </div>
  );
}
