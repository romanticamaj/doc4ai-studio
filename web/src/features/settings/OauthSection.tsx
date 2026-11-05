import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { RotateCcw } from "lucide-react";
import { useUpdateSettings } from "@/api/queries";
import type { OauthSettings } from "@/api/types";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { describeError } from "@/lib/errors";

/** Ranges as the server checks them (aidoc.config.OAUTH_RANGES; spec 2026-11-05 §9). */
export const OAUTH_LIMITS = { access_ttl_min: [5, 1440], refresh_ttl_days: [1, 365] } as const;
type RangeKey = keyof typeof OAUTH_LIMITS;

function RangeRow({ label, hint, unit, value, range, onValue }: {
  label: string; hint: string; unit: string; value: string; range: readonly [number, number]; onValue: (v: string) => void;
}) {
  const id = useId();
  const n = Number(value);
  const bad = value.trim() === "" || !Number.isInteger(n) || n < range[0] || n > range[1];
  return (
    <div className="grid gap-2 py-3.5 sm:grid-cols-[220px_minmax(0,1fr)] sm:items-center sm:gap-6">
      <div>
        <Label htmlFor={id}>{label}</Label>
        <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>
      </div>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Input id={id} type="number" inputMode="numeric" min={range[0]} max={range[1]} value={value} aria-invalid={bad}
                 aria-describedby={bad ? `${id}-err` : undefined} onChange={(e) => onValue(e.target.value)} className="w-32 tabular" />
          <span className="text-xs text-muted-foreground">{unit}</span>
        </div>
        {bad && (
          <p id={`${id}-err`} role="alert" className="mt-1 text-xs text-danger">
            請輸入 {range[0]}–{range[1]} 的整數
          </p>
        )}
      </div>
    </div>
  );
}

function SwitchRow({ label, hint, checked, onChange }: { label: string; hint: string; checked: boolean; onChange: (v: boolean) => void }) {
  const id = useId();
  return (
    <div className="grid gap-2 py-3.5 sm:grid-cols-[220px_minmax(0,1fr)] sm:items-center sm:gap-6">
      <div>
        <Label htmlFor={id}>{label}</Label>
        <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>
      </div>
      <div className="min-w-0">
        <Switch id={id} checked={checked} onCheckedChange={onChange} />
      </div>
    </div>
  );
}

/** 「OAuth 授權」: the [oauth] switches and token lifetimes; saved like the other sections (PUT /api/settings). */
export function OauthSection({ original }: { original: OauthSettings }) {
  const update = useUpdateSettings();
  const [flags, setFlags] = useState({ enabled: original.enabled, allow_dcr: original.allow_dcr, allow_cimd: original.allow_cimd });
  const [nums, setNums] = useState<Record<RangeKey, string>>({
    access_ttl_min: String(original.access_ttl_min), refresh_ttl_days: String(original.refresh_ttl_days),
  });
  useEffect(() => {
    setFlags({ enabled: original.enabled, allow_dcr: original.allow_dcr, allow_cimd: original.allow_cimd });
    setNums({ access_ttl_min: String(original.access_ttl_min), refresh_ttl_days: String(original.refresh_ttl_days) });
  }, [original]);

  // refresh days can never exceed the absolute limit (the server refuses refresh_ttl_days > refresh_max_days)
  const ranges: Record<RangeKey, readonly [number, number]> = {
    access_ttl_min: OAUTH_LIMITS.access_ttl_min,
    refresh_ttl_days: [OAUTH_LIMITS.refresh_ttl_days[0], Math.min(OAUTH_LIMITS.refresh_ttl_days[1], original.refresh_max_days)],
  };
  const valid = (Object.keys(ranges) as RangeKey[]).every((k) => {
    const n = Number(nums[k]);
    return nums[k].trim() !== "" && Number.isInteger(n) && n >= ranges[k][0] && n <= ranges[k][1];
  });
  const patch: Partial<OauthSettings> = {};
  for (const k of ["enabled", "allow_dcr", "allow_cimd"] as const) if (flags[k] !== original[k]) patch[k] = flags[k];
  for (const k of Object.keys(OAUTH_LIMITS) as RangeKey[]) if (valid && Number(nums[k]) !== original[k]) patch[k] = Number(nums[k]);
  const dirty = Object.keys(patch).length > 0 || (Object.keys(nums) as RangeKey[]).some((k) => nums[k] !== String(original[k]));
  const reset = () => {
    setFlags({ enabled: original.enabled, allow_dcr: original.allow_dcr, allow_cimd: original.allow_cimd });
    setNums({ access_ttl_min: String(original.access_ttl_min), refresh_ttl_days: String(original.refresh_ttl_days) });
  };

  return (
    <Panel>
      <form
        aria-label="OAuth 授權"
        onSubmit={(e) => {
          e.preventDefault();
          if (!valid || !Object.keys(patch).length) return;
          update.mutateAsync({ oauth: patch }).then(
            () => toast.success("已儲存"),
            (err) => toast.error(describeError(err)),
          );
        }}
      >
        <div className="border-b px-4 py-3">
          <h3 className="text-[13px] font-semibold">OAuth 授權</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            用戶端加入 /mcp 時不貼 token，改開瀏覽器到「授權連線」頁按允許。關閉後只剩 PAT，401 回到原本的格式。
          </p>
        </div>
        <div className="divide-y px-4">
          <SwitchRow label="啟用" hint="/oauth/*、well-known 與 401 的 resource_metadata" checked={flags.enabled}
                     onChange={(v) => setFlags((f) => ({ ...f, enabled: v }))} />
          <SwitchRow label="允許自動註冊（DCR）" hint="Cursor、Python SDK、MCP Inspector 用這個註冊" checked={flags.allow_dcr}
                     onChange={(v) => setFlags((f) => ({ ...f, allow_dcr: v }))} />
          <SwitchRow label="允許 CIMD" hint="client_id 是 https 網址的用戶端（Claude Code）" checked={flags.allow_cimd}
                     onChange={(v) => setFlags((f) => ({ ...f, allow_cimd: v }))} />
          <RangeRow label="access token 分鐘" hint="到期後用戶端自動以 refresh token 換新" unit="分鐘" value={nums.access_ttl_min}
                    range={OAUTH_LIMITS.access_ttl_min} onValue={(v) => setNums((n) => ({ ...n, access_ttl_min: v }))} />
          <RangeRow label="refresh 天數" hint={`閒置這麼多天就要重新授權（最長 ${original.refresh_max_days} 天）`} unit="天"
                    value={nums.refresh_ttl_days} range={ranges.refresh_ttl_days}
                    onValue={(v) => setNums((n) => ({ ...n, refresh_ttl_days: v }))} />
        </div>
        <div className="flex items-center justify-end gap-2 border-t px-4 py-3">
          {dirty && <span className="mr-auto text-xs text-muted-foreground">有未儲存的變更</span>}
          <Button type="button" variant="ghost" size="sm" disabled={!dirty} onClick={reset}>
            <RotateCcw /> 還原
          </Button>
          <Button type="submit" size="sm" disabled={!valid || !Object.keys(patch).length || update.isPending}>
            {update.isPending ? "儲存中…" : "儲存"}
          </Button>
        </div>
      </form>
    </Panel>
  );
}
