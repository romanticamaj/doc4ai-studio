import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { formatBytes } from "@/lib/format";

/** The text of the 「清除」 confirmation (spec 2026-10-29 §11.3); the second sentence only when copies go too. */
export function purgeText(records: number, copies: number, bytes: number, shared = 0): { title: string; body: string } {
  const title = `清除 ${records} 筆紀錄？`;
  // bytes = space actually freed; a copy that shares its data with a document that stays frees nothing
  const note = shared > 0 ? `，其中 ${shared} 個與仍在的文件共用內容，不會釋放空間` : "";
  const second = copies > 0 ? `也會刪除這些文件在 source\\ 的原始檔備份（${copies} 個檔案，${formatBytes(bytes)}${note}）。` : "";
  return { title, body: `${second}轉換結果已經不在了，這個動作無法復原。` };
}

/** Library → orphaned banner → 清除: nothing is deleted until 清除 inside the dialog is pressed. */
export function ConfirmPurgeDialog({
  open,
  onOpenChange,
  records,
  copies,
  bytes,
  shared = 0,
  loading,
  pending,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  records: number;
  copies: number;
  bytes: number;
  /** copies hardlinked with documents that stay (not in `bytes`) */
  shared?: number;
  /** the copy numbers are being fetched: 清除 waits for them (the warning must be current) */
  loading?: boolean;
  pending?: boolean;
  onConfirm: () => void;
}) {
  const t = purgeText(records, copies, bytes, shared);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="confirm-purge">
        <DialogHeader>
          <DialogTitle>{t.title}</DialogTitle>
          <DialogDescription>{loading ? "正在確認要一併刪除的原始檔備份…" : t.body}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button variant="destructive" disabled={pending || loading} onClick={onConfirm}>
            清除
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
