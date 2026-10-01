import { KeyRound, TriangleAlert } from "lucide-react";
import type { McpTokenCreated } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ConfigSnippets, CopyButton } from "./ConfigSnippets";

/**
 * The only place a token's plaintext is ever shown (creation or rotation). The parent owns `result` and sets it to
 * null on close, so nothing keeps the string afterwards.
 */
export function TokenRevealDialog({ result, onClose }: { result: McpTokenCreated | null; onClose: () => void }) {
  if (!result) return null;
  const rotated = Boolean(result.revoked);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-2xl"
        onInteractOutside={(e) => e.preventDefault()}         // a stray click must not throw the token away
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyRound className="size-4 text-primary" strokeWidth={1.75} />
            {rotated ? "新的 token（已輪替）" : "token 已建立"}
          </DialogTitle>
          <DialogDescription>
            {result.record.name}
            {rotated && "：舊的 token 已立即失效，請更新用戶端設定。"}
          </DialogDescription>
        </DialogHeader>

        <div className="rounded-lg border border-l-[3px] border-l-primary bg-muted/40 p-3">
          <label htmlFor="mcp-token-plain" className="text-xs text-muted-foreground">
            token
          </label>
          <div className="mt-1 flex items-center gap-2">
            <input
              id="mcp-token-plain"
              readOnly
              aria-label="新的 token"
              value={result.token}
              onFocus={(e) => e.currentTarget.select()}
              className="h-8 min-w-0 flex-1 rounded-md border bg-background px-2 font-mono text-[12px] tracking-tight outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            />
            <CopyButton text={result.token} />
          </div>
          <p className="mt-2 flex items-center gap-1.5 text-xs font-medium text-warn">
            <TriangleAlert className="size-3.5 shrink-0" />
            關閉後無法再次查看這把 token
          </p>
        </div>

        {result.snippets.length > 0 && (
          <section aria-label="連線設定" className="min-w-0">
            <h3 className="mb-2 text-[13px] font-semibold">貼到用戶端的設定</h3>
            <ConfigSnippets snippets={result.snippets} />
          </section>
        )}

        <DialogFooter>
          <Button onClick={onClose}>我已複製，關閉</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
