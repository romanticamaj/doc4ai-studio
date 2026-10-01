import { TriangleAlert } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { forgetIssuedTokens, useCreateToken, type TokenCreateBody } from "@/api/mcp";
import { useSettings } from "@/api/queries";
import type { McpScope, McpTokenCreated } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { NumberField } from "@/features/settings/NumberField";
import { describeError } from "@/lib/errors";
import { cn } from "@/lib/utils";
import { DEFAULT_SCOPES, SCOPE_META, TTL_CHOICES } from "./tokenStatus";

const ALL_SCOPES = Object.keys(SCOPE_META) as McpScope[];
const NEVER = 0;
const RISK_TEXT: Partial<Record<McpScope, string>> = {
  "doc4ai:convert:local": "轉換主機上白名單資料夾裡的檔案",
  "doc4ai:manage": "取消工作、重新轉換文件",
};

/** The presets the server allows, plus the server's own default and maximum (so both are always selectable). */
export function ttlChoices(def: number | undefined, max: number | undefined, allowNever: boolean): number[] {
  const days = new Set<number>(TTL_CHOICES.filter((d) => !max || d <= max));
  if (def && (!max || def <= max)) days.add(def);
  if (max) days.add(max);
  return [...[...days].sort((a, b) => a - b), ...(allowNever ? [NEVER] : [])];
}

export function CreateTokenDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (result: McpTokenCreated) => void;
}) {
  const settings = useSettings();
  const mcp = settings.data?.mcp;
  const create = useCreateToken();
  const qc = useQueryClient();
  const ids = useId();
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<Set<McpScope>>(new Set(DEFAULT_SCOPES));
  const [ttl, setTtl] = useState<number>(90);
  const [note, setNote] = useState("");
  const [rate, setRate] = useState<number | null>(null);

  // the server's default TTL, when it is one of the choices
  const defaultTtl = mcp?.default_token_ttl_days;
  const maxTtl = mcp?.max_token_ttl_days;
  useEffect(() => {
    if (defaultTtl) setTtl(maxTtl ? Math.min(defaultTtl, maxTtl) : defaultTtl);
  }, [defaultTtl, maxTtl]);

  useEffect(() => {
    if (!open) {
      setName("");
      setScopes(new Set(DEFAULT_SCOPES));
      setNote("");
      setRate(null);
    }
  }, [open]);

  const highRisk = ALL_SCOPES.filter((s) => scopes.has(s) && SCOPE_META[s].risk === "high");
  const choices = ttlChoices(mcp?.default_token_ttl_days, mcp?.max_token_ttl_days, Boolean(mcp?.allow_no_expiry));

  const toggle = (s: McpScope) =>
    setScopes((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || create.isPending) return;
    const body: TokenCreateBody = { name: name.trim(), scopes: ALL_SCOPES.filter((s) => scopes.has(s)), expires_in_days: ttl };
    if (note.trim()) body.note = note.trim();
    if (rate != null) body.rate_limit_per_min = rate;
    try {
      const result = await create.mutateAsync(body);
      create.reset(); // the plaintext lives on only in the reveal dialog's props, not in the mutation cache
      forgetIssuedTokens(qc);
      onCreated(result);
    } catch (err) {
      toast.error(describeError(err));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg">
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>建立 token</DialogTitle>
            <DialogDescription>給一個 AI 用戶端專用的 token。建立後只會顯示一次。</DialogDescription>
          </DialogHeader>

          <div className="grid gap-1.5">
            <Label htmlFor={`${ids}-name`}>名稱</Label>
            <Input
              id={`${ids}-name`}
              value={name}
              maxLength={80}
              placeholder="例如 Claude Code @ 筆電"
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
          </div>

          <fieldset className="grid gap-1.5">
            <legend className="mb-1.5 text-sm font-medium">權限範圍</legend>
            <ul className="grid gap-1 rounded-lg border p-1">
              {ALL_SCOPES.map((s) => {
                const meta = SCOPE_META[s];
                const locked = s === "doc4ai:read";
                const id = `${ids}-${s}`;
                return (
                  <li key={s}>
                    <label
                      htmlFor={id}
                      className={cn(
                        "flex cursor-pointer items-start gap-2.5 rounded-md px-2 py-1.5 hover:bg-muted/60",
                        locked && "cursor-default hover:bg-transparent",
                      )}
                    >
                      <input
                        id={id}
                        type="checkbox"
                        aria-label={s}
                        className="mt-0.5 size-3.5 accent-[var(--primary)]"
                        checked={scopes.has(s)}
                        disabled={locked}
                        onChange={() => toggle(s)}
                      />
                      <span className="min-w-0">
                        <span className={cn("font-mono text-[12px]", meta.risk === "high" && "text-danger")}>{s}</span>
                        <span className="block text-xs text-muted-foreground">
                          {meta.label}：{meta.hint}
                          {locked && "（每把 token 都有）"}
                        </span>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
            {highRisk.length > 0 && (
              <p className="flex items-start gap-1.5 text-xs text-danger">
                <TriangleAlert className="mt-px size-3.5 shrink-0" />
                高風險：這把 token 能{highRisk.map((s) => RISK_TEXT[s]).join("，也能")}。只給你信任的用戶端。
              </p>
            )}
          </fieldset>

          <div className="grid gap-1.5">
            <span id={`${ids}-ttl`} className="text-sm font-medium">
              到期
            </span>
            <div role="radiogroup" aria-labelledby={`${ids}-ttl`} className="flex flex-wrap gap-1">
              {choices.map((d) => {
                const label = d === NEVER ? "永不過期" : `${d} 天`;
                const on = ttl === d;
                return (
                  <button
                    key={d}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    onClick={() => setTtl(d)}
                    className={cn(
                      "h-7 rounded-md border px-2.5 text-xs transition-colors focus-visible:outline-2 focus-visible:outline-ring",
                      on ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted",
                    )}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
            {ttl === NEVER && <p className="text-xs text-warn">永不過期的 token 外洩後只能靠手動撤銷。</p>}
          </div>

          <div className="grid gap-3 sm:grid-cols-[1fr_9rem]">
            <div className="grid gap-1.5">
              <Label htmlFor={`${ids}-note`}>備註</Label>
              <Textarea id={`${ids}-note`} value={note} maxLength={500} rows={2} onChange={(e) => setNote(e.target.value)} />
            </div>
            <div className="grid content-start gap-1.5">
              <Label htmlFor={`${ids}-rate`}>每分鐘上限</Label>
              {rate == null ? (
                <Button type="button" variant="outline" size="sm" className="justify-start" onClick={() => setRate(mcp?.rate_limit_per_min ?? 60)}>
                  用全域設定（{mcp?.rate_limit_per_min ?? 60}）
                </Button>
              ) : (
                <NumberField id={`${ids}-rate`} value={rate} min={1} max={100000} onValue={setRate} />
              )}
            </div>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button type="submit" disabled={!name.trim() || create.isPending}>
              建立
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
