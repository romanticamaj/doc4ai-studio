import { KeyRound, MoreHorizontal, Plus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { forgetIssuedTokens, useMcpTokens, usePatchToken, useRevokeToken, useRotateToken } from "@/api/mcp";
import type { McpToken, McpTokenCreated } from "@/api/types";
import { Panel, PanelHeader } from "@/components/Panel";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { describeError } from "@/lib/errors";
import { formatDateTime, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import { CreateTokenDialog } from "./CreateTokenDialog";
import { ScopeBadges } from "./ScopeBadges";
import { TokenRevealDialog } from "./TokenRevealDialog";
import { expiryState } from "./tokenStatus";

const TONE_TEXT = { ok: "text-muted-foreground", warn: "text-warn", danger: "text-danger", neutral: "text-muted-foreground" } as const;

type Pending = { kind: "rotate" | "revoke" | "edit"; token: McpToken } | null;

export function TokensTab() {
  const tokens = useMcpTokens();
  const [creating, setCreating] = useState(false);
  const [reveal, setReveal] = useState<McpTokenCreated | null>(null);
  const [pending, setPending] = useState<Pending>(null);

  const createButton = (
    <Button size="sm" onClick={() => setCreating(true)}>
      <Plus /> 建立 token
    </Button>
  );

  return (
    <>
      <Panel>
        <PanelHeader
          title="Tokens"
          description="每個 AI 用戶端一把。token 只對 /mcp 有效，拿不到這個管理後台。"
          actions={createButton}
        />
        {tokens.isPending ? (
          <div className="grid gap-2 p-4">
            <Skeleton className="h-9" />
            <Skeleton className="h-9" />
          </div>
        ) : tokens.isError ? (
          <div className="p-4">
            <ErrorState title="無法讀取 token" error={tokens.error} onRetry={() => tokens.refetch()} />
          </div>
        ) : tokens.data.length === 0 ? (
          <div className="p-4">
            <EmptyState
              icon={KeyRound}
              title="還沒有 token"
              description="按右上角「建立 token」，再把它貼到 Claude Code、Cursor 或 VS Code 的 MCP 設定裡。"
            />
          </div>
        ) : (
          <TokenTable tokens={tokens.data} onAction={setPending} />
        )}
      </Panel>

      <CreateTokenDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={(r) => {
          setCreating(false);
          setReveal(r);
        }}
      />
      <TokenRevealDialog result={reveal} onClose={() => setReveal(null)} />
      <RotateDialog pending={pending?.kind === "rotate" ? pending.token : null} onDone={(r) => { setPending(null); if (r) setReveal(r); }} />
      <RevokeDialog pending={pending?.kind === "revoke" ? pending.token : null} onDone={() => setPending(null)} />
      <EditDialog pending={pending?.kind === "edit" ? pending.token : null} onDone={() => setPending(null)} />
    </>
  );
}

function TokenTable({ tokens, onAction }: { tokens: McpToken[]; onAction: (p: Pending) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[880px] text-[13px]">
        <thead className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
          <tr>
            <th className="px-4 py-2 font-medium">名稱</th>
            <th className="px-3 py-2 font-medium">前綴</th>
            <th className="px-3 py-2 font-medium">Scopes</th>
            <th className="px-3 py-2 font-medium">建立</th>
            <th className="px-3 py-2 font-medium">到期</th>
            <th className="px-3 py-2 font-medium">最後使用</th>
            <th className="px-3 py-2 text-right font-medium">24h 呼叫</th>
            <th className="px-3 py-2 font-medium">狀態</th>
            <th className="px-3 py-2">
              <span className="sr-only">動作</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {tokens.map((t) => {
            const exp = expiryState(t);
            const inactive = t.status !== "active";
            return (
              <tr key={t.id} className={cn("align-top", inactive && "text-muted-foreground")}>
                <td className="max-w-[16rem] px-4 py-2.5">
                  <span className="block truncate font-medium">{t.name}</span>
                  {t.note && <span className="block truncate text-xs text-muted-foreground">{t.note}</span>}
                  {t.revoked_reason && <span className="block truncate text-xs text-muted-foreground">撤銷原因：{t.revoked_reason}</span>}
                </td>
                <td className="px-3 py-2.5 font-mono text-xs whitespace-nowrap">{t.prefix}…</td>
                <td className="px-3 py-2.5">
                  <ScopeBadges scopes={t.scopes} />
                </td>
                <td className="px-3 py-2.5 text-xs whitespace-nowrap tabular" title={formatDateTime(t.created_at)}>
                  {formatRelative(t.created_at)}
                </td>
                <td className={cn("px-3 py-2.5 text-xs whitespace-nowrap", TONE_TEXT[exp.tone])} title={formatDateTime(t.expires_at)}>
                  {exp.label}
                </td>
                <td className="px-3 py-2.5 text-xs">
                  {t.last_used_at ? (
                    <>
                      <span className="block whitespace-nowrap tabular" title={formatDateTime(t.last_used_at)}>
                        {formatRelative(t.last_used_at)}
                      </span>
                      <span className="block max-w-[12rem] truncate text-muted-foreground">
                        {[t.last_client, t.last_used_ip].filter(Boolean).join("，")}
                      </span>
                    </>
                  ) : (
                    <span className="text-muted-foreground">還沒用過</span>
                  )}
                </td>
                <td className="px-3 py-2.5 text-right text-xs whitespace-nowrap tabular">
                  {t.calls_24h}
                  {t.errors_24h > 0 && <span className="ml-1 text-danger">（{t.errors_24h} 錯誤）</span>}
                </td>
                <td className="px-3 py-2.5">
                  <StatusBadge status={t.status} />
                </td>
                <td className="px-3 py-2 text-right">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button size="icon-sm" variant="ghost" aria-label="動作">
                        <MoreHorizontal />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => onAction({ kind: "edit", token: t })}>編輯名稱與備註</DropdownMenuItem>
                      <DropdownMenuItem disabled={t.status === "revoked"} onSelect={() => onAction({ kind: "rotate", token: t })}>
                        輪替
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        variant="destructive"
                        disabled={t.status === "revoked"}
                        onSelect={() => onAction({ kind: "revoke", token: t })}
                      >
                        撤銷
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RotateDialog({ pending, onDone }: { pending: McpToken | null; onDone: (r: McpTokenCreated | null) => void }) {
  const rotate = useRotateToken();
  const qc = useQueryClient();
  return (
    <Dialog open={pending != null} onOpenChange={(o) => !o && onDone(null)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>輪替「{pending?.name}」？</DialogTitle>
          <DialogDescription>會產生一把同權限的新 token，舊的立即失效；用到它的用戶端要換上新 token。</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onDone(null)}>
            取消
          </Button>
          <Button
            disabled={rotate.isPending}
            onClick={async () => {
              try {
                const r = await rotate.mutateAsync(pending!.id);
                rotate.reset(); // keep the new plaintext out of the mutation cache
                forgetIssuedTokens(qc);
                onDone(r);
              } catch (e) {
                toast.error(describeError(e));
              }
            }}
          >
            確認輪替
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function RevokeDialog({ pending, onDone }: { pending: McpToken | null; onDone: () => void }) {
  const revoke = useRevokeToken();
  const [reason, setReason] = useState("");
  return (
    <Dialog
      open={pending != null}
      onOpenChange={(o) => {
        if (!o) {
          setReason("");
          onDone();
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>撤銷「{pending?.name}」？</DialogTitle>
          <DialogDescription>撤銷後下一個請求就會被拒絕（401），無法復原。</DialogDescription>
        </DialogHeader>
        <div className="grid gap-1.5">
          <Label htmlFor="mcp-revoke-reason">原因</Label>
          <Input id="mcp-revoke-reason" value={reason} maxLength={200} placeholder="選填，例如 筆電遺失" onChange={(e) => setReason(e.target.value)} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onDone}>
            取消
          </Button>
          <Button
            variant="destructive"
            disabled={revoke.isPending}
            onClick={async () => {
              try {
                await revoke.mutateAsync({ id: pending!.id, reason: reason.trim() || undefined });
                toast.success("已撤銷");
                setReason("");
                onDone();
              } catch (e) {
                toast.error(describeError(e));
              }
            }}
          >
            確認撤銷
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditDialog({ pending, onDone }: { pending: McpToken | null; onDone: () => void }) {
  const patch = usePatchToken();
  return (
    <Dialog open={pending != null} onOpenChange={(o) => !o && onDone()}>
      <DialogContent>
        {pending && (
          <EditForm
            key={pending.id}
            token={pending}
            busy={patch.isPending}
            onCancel={onDone}
            onSave={async (name, note) => {
              try {
                await patch.mutateAsync({ id: pending.id, name, note: note || null });
                toast.success("已儲存");
                onDone();
              } catch (e) {
                toast.error(describeError(e));
              }
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function EditForm({ token, busy, onCancel, onSave }: { token: McpToken; busy: boolean; onCancel: () => void; onSave: (name: string, note: string) => void }) {
  const [name, setName] = useState(token.name);
  const [note, setNote] = useState(token.note ?? "");
  return (
    <form
      className="grid gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) onSave(name.trim(), note.trim());
      }}
    >
      <DialogHeader>
        <DialogTitle>編輯 token</DialogTitle>
        <DialogDescription>權限範圍不能改；要換權限請建立新的 token。</DialogDescription>
      </DialogHeader>
      <div className="grid gap-1.5">
        <Label htmlFor="mcp-edit-name">名稱</Label>
        <Input id="mcp-edit-name" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="mcp-edit-note">備註</Label>
        <Textarea id="mcp-edit-note" value={note} maxLength={500} rows={2} onChange={(e) => setNote(e.target.value)} />
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>
          取消
        </Button>
        <Button type="submit" disabled={!name.trim() || busy}>
          儲存
        </Button>
      </DialogFooter>
    </form>
  );
}
