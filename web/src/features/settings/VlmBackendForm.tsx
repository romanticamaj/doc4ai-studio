import { useEffect, useState, type ReactNode } from "react";
import { Info, Loader2, PlugZap, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Meter } from "@/components/Meter";
import { Sparkline } from "@/features/mcp/Sparkline";
import { NumberField } from "@/features/settings/NumberField";
import type { EngineInfo, SettingsPatch, VlmBackend, VlmSettings, VlmUsage } from "@/api/types";
import { endpointHost, isLoopbackUrl } from "./isLoopbackUrl";
import { cn } from "@/lib/utils";

const BACKENDS: Array<{ value: VlmBackend; label: string }> = [
  { value: "none", label: "none（不使用）" },
  { value: "openai", label: "openai（OpenAI 相容端點）" },
  { value: "local", label: "local（本機 llama.cpp）" },
];

const PRESETS: Array<{ value: VlmSettings["local_preset"]; label: string }> = [
  { value: "paddleocr-vl-1.6", label: "PaddleOCR-VL-1.6" },
  { value: "qwen2.5-vl-3b", label: "Qwen2.5-VL-3B" },
  { value: "custom", label: "自訂 GGUF" },
];

/** The changed [vlm] keys; `api_key` only when the user typed a new one (the stored key reads "***"). */
export function vlmPatch(original: VlmSettings, edited: VlmSettings, typedKey: string): Partial<VlmSettings> {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(edited) as Array<keyof VlmSettings>) {
    if (k === "api_key") continue;
    if (edited[k] !== original[k]) out[k] = edited[k];
  }
  if (typedKey.trim()) out.api_key = typedKey.trim();
  return out as Partial<VlmSettings>;
}

/** The consent text (spec §4.6): blue info for an endpoint on this computer, red warning for anything else. */
export function ConsentNote({ id, baseUrl }: { id: string; baseUrl: string }) {
  const loop = isLoopbackUrl(baseUrl);
  const host = endpointHost(baseUrl);
  if (loop) {
    return (
      <p id={id} data-tone="info" className="flex items-start gap-1.5 text-xs text-info">
        <Info className="mt-0.5 size-3.5 shrink-0" />
        端點在本機（{host}）：我了解開啟後，被送到此引擎的頁面影像會交給這台電腦上的這個服務處理。
      </p>
    );
  }
  return (
    <p id={id} data-tone="warn" className="flex items-start gap-1.5 text-xs text-danger">
      <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
      我了解開啟後，被送到此引擎的頁面影像會傳到 {host || "（尚未設定的端點）"}，也就是離開這台電腦。
    </p>
  );
}

function Row({ label, hint, htmlFor, children }: { label: string; hint?: string; htmlFor?: string; children: ReactNode }) {
  return (
    <div className="grid gap-2 py-3 sm:grid-cols-[200px_minmax(0,1fr)] sm:items-center sm:gap-6">
      <div>
        <Label htmlFor={htmlFor}>{label}</Label>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function VlmBackendForm({
  settings,
  info,
  onSave,
  saving,
  onTest,
  testing,
  usage,
  log,
}: {
  settings: VlmSettings;
  info: EngineInfo | undefined;
  onSave: (patch: SettingsPatch) => void | Promise<unknown>;
  saving?: boolean;
  onTest?: () => void;
  testing?: boolean;
  usage?: VlmUsage;
  log?: ReactNode;
}) {
  const [v, setV] = useState<VlmSettings>(settings);
  const [typedKey, setTypedKey] = useState("");
  useEffect(() => {
    setV(settings);
    setTypedKey("");
  }, [settings]);
  const set = <K extends keyof VlmSettings>(key: K, value: VlmSettings[K]) => setV((s) => ({ ...s, [key]: value }));
  const patch = vlmPatch(settings, v, typedKey);
  const dirty = Object.keys(patch).length > 0;
  const localSupported = info?.local_supported !== false;
  const canConsent = Boolean(v.base_url.trim() && v.model.trim());
  const keyStored = settings.api_key === "***";

  const setBackend = (b: VlmBackend) =>
    setV((s) => ({ ...s, backend: b, remote_enabled: b === "openai" ? s.remote_enabled : false }));

  const num = (key: keyof VlmSettings, id: string, min: number, max: number, unit?: string) => (
    <div className="flex items-center gap-2">
      <NumberField id={id} min={min} max={max} value={v[key] as number} onValue={(n) => set(key, n as never)} className="w-32 tabular" />
      {unit && <span className="text-xs text-muted-foreground">{unit}</span>}
    </div>
  );

  const usedToday = info?.budget?.used_today ?? usage?.today.pages ?? 0;
  const limit = info?.budget?.limit ?? v.remote_daily_page_budget;
  const spark = sparkValues(usage);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void onSave({ vlm: patch });
      }}
    >
      <div className="divide-y px-4">
        <Row label="後端" hint="none = 完全不使用；openai = 任何 OpenAI 相容的視覺端點" htmlFor="vlm-backend">
          <select
            id="vlm-backend"
            aria-label="後端"
            value={v.backend}
            onChange={(e) => setBackend(e.target.value as VlmBackend)}
            className="h-8 w-full max-w-xs rounded-md border bg-background px-2 text-[13px] focus-visible:outline-2 focus-visible:outline-ring"
          >
            {BACKENDS.map((b) => {
              const off = b.value === "local" && !localSupported;
              return (
                <option key={b.value} value={b.value} disabled={off} aria-disabled={off ? "true" : undefined}>
                  {off ? `${b.label}（本機後端尚未支援此平台）` : b.label}
                </option>
              );
            })}
          </select>
        </Row>

        {v.backend === "none" && (
          <p className="py-3 text-xs text-muted-foreground">尚未選擇後端：VLM 不會出現在路由或逐頁修復裡，現有行為完全不變。</p>
        )}

        {v.backend === "local" && (
          <div className="py-3 text-xs">
            {!localSupported && (
              <p className="flex items-start gap-1.5 text-warn">
                <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
                本機後端尚未支援此平台（spike 結果見 docs/superpowers/spikes/2026-10-vlm-ocr.md）。可改用 openai 後端接本機的 LM Studio／Ollama。
              </p>
            )}
            <Row label="Preset" htmlFor="vlm-preset">
              <select
                id="vlm-preset"
                value={v.local_preset}
                disabled={!localSupported}
                onChange={(e) => set("local_preset", e.target.value as VlmSettings["local_preset"])}
                className="h-8 w-full max-w-xs rounded-md border bg-background px-2 text-[13px]"
              >
                {PRESETS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </Row>
            <p className="text-muted-foreground">此引擎不抽取圖片：頁面上的圖會標成「image missing」。</p>
          </div>
        )}

        {v.backend === "openai" && (
          <>
            <Row label="base_url" hint="例：http://127.0.0.1:11434/v1（Ollama）、http://127.0.0.1:1234/v1（LM Studio）" htmlFor="vlm-url">
              <Input id="vlm-url" value={v.base_url} onChange={(e) => set("base_url", e.target.value)} placeholder="https://…/v1" className="font-mono" />
            </Row>
            <Row label="model" htmlFor="vlm-model">
              <Input id="vlm-model" value={v.model} onChange={(e) => set("model", e.target.value)} className="font-mono" />
            </Row>
            <Row label="API key" hint={`建議改用環境變數 ${v.api_key_env || "AIDOC_VLM_API_KEY"}；留空 = 不變`} htmlFor="vlm-key">
              <Input
                id="vlm-key"
                type="password"
                autoComplete="off"
                value={typedKey}
                onChange={(e) => setTypedKey(e.target.value)}
                placeholder={keyStored ? "***" : info?.key_set ? `已由 ${v.api_key_env} 提供` : ""}
                className="font-mono"
              />
            </Row>
            <Row label="單頁逾時" htmlFor="vlm-timeout">{num("request_timeout_s", "vlm-timeout", 10, 600, "秒")}</Row>
            <Row label="並行請求" htmlFor="vlm-conc">{num("max_concurrent", "vlm-conc", 1, 8)}</Row>
            <Row label="單檔頁數上限" hint="頁數更多的 PDF 不會整份送到 VLM（0 = 不限）" htmlFor="vlm-cap">
              {num("remote_max_pages_per_doc", "vlm-cap", 0, 1000000, "頁")}
            </Row>
            <Row label="每日頁數預算" hint="0 = 不限" htmlFor="vlm-budget">
              {num("remote_daily_page_budget", "vlm-budget", 0, 10000000, "頁")}
            </Row>
            <div className="flex flex-col gap-2 py-3">
              <div className="flex items-start gap-3">
                <Switch
                  id="vlm-consent"
                  aria-labelledby="vlm-consent-note"
                  checked={v.remote_enabled}
                  disabled={!canConsent}
                  onCheckedChange={(on) => set("remote_enabled", on)}
                  className="mt-0.5"
                />
                <ConsentNote id="vlm-consent-note" baseUrl={v.base_url} />
              </div>
              {!canConsent && <p className="pl-12 text-xs text-muted-foreground">先填 base_url 與 model。</p>}
            </div>
            {info?.budget && (
              <div className="flex items-center gap-4 py-3">
                <div className="min-w-0 flex-1">
                  <Meter
                    value={limit > 0 ? (usedToday / limit) * 100 : 0}
                    tone={limit > 0 && usedToday / limit > 0.9 ? "warn" : "info"}
                    label="今日 VLM 頁數"
                  />
                  <p className="mt-1.5 text-xs text-muted-foreground tabular">
                    今日 {usedToday} 頁{limit > 0 ? ` / 預算 ${limit} 頁` : "（不限）"} · {info.budget.requests_today} 個請求
                  </p>
                </div>
                {spark.length > 1 && <Sparkline values={spark} label="30 天每日 VLM 頁數" />}
              </div>
            )}
          </>
        )}

        {v.backend !== "none" && (
          <>
            <Row label="當作最後一道 fallback" hint="所有 PDF／圖片路由的最後一個引擎" htmlFor="vlm-fallback">
              <Switch id="vlm-fallback" checked={v.use_as_fallback} onCheckedChange={(on) => set("use_as_fallback", on)} />
            </Row>
            <Row label="用於逐頁修復" hint="文字層損壞的頁，在 docling／mineru 都修不好之後" htmlFor="vlm-repair">
              <Switch id="vlm-repair" checked={v.use_for_repair} onCheckedChange={(on) => set("use_for_repair", on)} />
            </Row>
          </>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t px-4 py-3">
        <Button type="submit" size="sm" disabled={saving}>
          儲存
        </Button>
        {v.backend === "openai" && onTest && (
          <Button type="button" size="sm" variant="outline" onClick={onTest} disabled={testing || dirty} title={dirty ? "先儲存再測試" : undefined}>
            {testing ? <Loader2 className="animate-spin" /> : <PlugZap />}
            測試連線
          </Button>
        )}
        {dirty && <span className={cn("text-xs text-muted-foreground")}>有未儲存的變更</span>}
      </div>
      {log}
    </form>
  );
}

function sparkValues(usage: VlmUsage | undefined): number[] {
  if (!usage?.days.length) return [];
  const byDay = new Map<string, number>();
  for (const r of usage.days) byDay.set(r.day, (byDay.get(r.day) ?? 0) + r.pages);
  return [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([, n]) => n);
}
