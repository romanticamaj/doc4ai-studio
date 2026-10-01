import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Cpu, KeyRound, Lock, RotateCcw, TriangleAlert } from "lucide-react";
import { Page, PageHeader } from "@/components/layout/Page";
import { Panel, PanelHeader } from "@/components/Panel";
import { Meter } from "@/components/Meter";
import { ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useSettings, useSystem, useUpdateSettings } from "@/api/queries";
import { ApiError, getToken, setToken } from "@/api/client";
import type { EngineName, Settings } from "@/api/types";
import { useEventStream } from "@/events/EventStreamProvider";
import { EngineCard } from "@/features/settings/EngineCard";
import { NumberField } from "@/features/settings/NumberField";
import { ListField } from "@/features/settings/ListField";
import { diffSettings } from "@/features/settings/diffSettings";
import { describeError } from "@/lib/errors";
import { formatBytes, gb } from "@/lib/format";
import { cn } from "@/lib/utils";

const ENGINES: EngineName[] = ["markitdown", "docling", "mineru"];
const MB = 1024 * 1024;

export default function SettingsPage() {
  const sys = useSystem();
  const settings = useSettings();
  const unauthorized = [sys.error, settings.error].some((e) => e instanceof ApiError && e.status === 401);

  return (
    <Page>
      <PageHeader title="Settings" description="轉換引擎的安裝狀態、GPU，以及 aidoc.toml 裡的預設值。" />
      <div className="flex flex-col gap-8">
        {unauthorized && <TokenCard highlight />}

        <section aria-labelledby="engines-h">
          <SectionTitle id="engines-h" title="轉換引擎" description="每個引擎有自己的 Python 環境；安裝會執行 uv sync、下載模型並做自我檢查。" />
          {sys.isError && !unauthorized ? (
            <ErrorState title="無法讀取系統狀態" error={sys.error} onRetry={() => sys.refetch()} />
          ) : (
            <div className="grid gap-3 md:grid-cols-3">
              {ENGINES.map((e) =>
                sys.isPending ? <Skeleton key={e} className="h-44 rounded-[10px]" /> : <EngineCard key={e} name={e} info={sys.data?.engines[e]} />,
              )}
            </div>
          )}
        </section>

        <section aria-labelledby="machine-h">
          <SectionTitle id="machine-h" title="這台電腦" />
          <div className="grid gap-3 md:grid-cols-2">
            <Panel className="p-4">
              <div className="flex items-center gap-2 text-[13px] font-semibold">
                <Cpu className="size-4 text-muted-foreground" strokeWidth={1.75} /> GPU
              </div>
              {sys.isPending ? (
                <Skeleton className="mt-3 h-10" />
              ) : sys.data?.gpu ? (
                <>
                  <p className="mt-2 text-[13px]">{sys.data.gpu.name}</p>
                  <Meter
                    className="mt-3"
                    value={(sys.data.gpu.mem_used / sys.data.gpu.mem_total) * 100}
                    tone={sys.data.gpu.mem_used / sys.data.gpu.mem_total > 0.9 ? "warn" : "info"}
                    label="顯示記憶體"
                  />
                  <p className="mt-1.5 text-xs text-muted-foreground tabular">
                    顯示記憶體 {gb(sys.data.gpu.mem_used)} / {gb(sys.data.gpu.mem_total)} GB
                  </p>
                </>
              ) : (
                <p className="mt-2 text-[13px] text-muted-foreground">沒有偵測到 NVIDIA GPU；docling 與 mineru 會很慢或無法使用。</p>
              )}
            </Panel>
            <Panel className="p-4">
              <p className="text-[13px] font-semibold">儲存空間</p>
              {sys.isPending ? (
                <Skeleton className="mt-3 h-10" />
              ) : (
                <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
                  <dt className="text-muted-foreground">輸出目錄</dt>
                  <dd className="truncate font-mono" title={sys.data?.output_dir}>
                    {sys.data?.output_dir ?? "—"}
                  </dd>
                  <dt className="text-muted-foreground">可用空間</dt>
                  <dd className="tabular">{formatBytes(sys.data?.disk_free)}</dd>
                  <dt className="text-muted-foreground">aidoc</dt>
                  <dd className="font-mono">{sys.data?.version ?? "—"}</dd>
                  {sys.data && !sys.data.long_paths_enabled && (
                    <dd className="col-span-2 mt-1 flex items-start gap-1.5 text-warn">
                      <TriangleAlert className="mt-px size-3.5 shrink-0" /> Windows 長路徑未啟用：很深的輸出路徑可能寫入失敗。
                    </dd>
                  )}
                </dl>
              )}
            </Panel>
          </div>
        </section>

        <section aria-labelledby="defaults-h">
          <SectionTitle id="defaults-h" title="轉換預設值" description="存到 aidoc.toml，立即套用到之後的工作。" />
          {settings.isError && !unauthorized ? (
            <ErrorState title="無法讀取設定" error={settings.error} onRetry={() => settings.refetch()} />
          ) : settings.data ? (
            <SettingsForm original={settings.data} />
          ) : (
            <Skeleton className="h-72 rounded-[10px]" />
          )}
        </section>

        <section aria-labelledby="mcp-h">
          <SectionTitle id="mcp-h" title="MCP" description="其他 AI 透過 /mcp 使用這個 Library 的規則。token 本身在 MCP 頁管理。" />
          {settings.data ? <McpSettingsForm original={settings.data} /> : !settings.isError && <Skeleton className="h-72 rounded-[10px]" />}
        </section>

        {!unauthorized && <TokenCard />}
      </div>
    </Page>
  );
}

function SectionTitle({ id, title, description }: { id: string; title: string; description?: string }) {
  return (
    <div className="mb-3">
      <h2 id={id} className="text-[15px] font-semibold">
        {title}
      </h2>
      {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
    </div>
  );
}

function Field({ label, hint, htmlFor, children }: { label: string; hint?: string; htmlFor?: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-2 py-3.5 sm:grid-cols-[220px_minmax(0,1fr)] sm:items-center sm:gap-6">
      <div>
        <Label htmlFor={htmlFor}>{label}</Label>
        {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function SettingsForm({ original }: { original: Settings }) {
  const [edited, setEdited] = useState<Settings>(original);
  const update = useUpdateSettings();
  useEffect(() => setEdited(original), [original]);
  const patch = diffSettings(original, edited);
  const dirty = Object.keys(patch).length > 0;

  const set = <S extends keyof Settings, K extends keyof Settings[S]>(section: S, key: K, value: Settings[S][K]) =>
    setEdited((e) => ({ ...e, [section]: { ...e[section], [key]: value } }));

  const save = () =>
    update.mutateAsync(patch).then(
      () => toast.success("已儲存"),
      (e) => toast.error(describeError(e)),
    );

  return (
    <Panel>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (dirty) void save();
        }}
      >
        <div className="divide-y px-4">
          <Field label="預設語言" hint="OCR 與版面分析的語言" htmlFor="set-lang">
            <div role="radiogroup" aria-label="預設語言" className="inline-grid grid-cols-2 gap-1 rounded-lg bg-muted p-1">
              {(
                [
                  ["cht", "繁中＋英文"],
                  ["en", "英文"],
                ] as const
              ).map(([v, l]) => (
                <button
                  key={v}
                  type="button"
                  role="radio"
                  id={v === "cht" ? "set-lang" : undefined}
                  aria-checked={edited.general.lang === v}
                  onClick={() => set("general", "lang", v)}
                  className={cn(
                    "h-7 rounded-md px-3 text-[13px] text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring",
                    edited.general.lang === v && "bg-card font-medium text-foreground shadow-panel",
                  )}
                >
                  {l} <span className="ml-0.5 font-mono text-[11px] opacity-60">{v}</span>
                </button>
              ))}
            </div>
          </Field>
          <Field label="輸出目錄" hint="相對路徑以專案根目錄為準" htmlFor="set-out">
            <Input id="set-out" value={edited.general.output_dir} onChange={(e) => set("general", "output_dir", e.target.value)} className="font-mono text-[12.5px]" spellCheck={false} />
          </Field>
          <Field label="工作副本保留天數" hint="轉換成功後保留原始檔副本的天數" htmlFor="set-days">
            <NumberField id="set-days" min={0} max={3650} value={edited.general.work_retention_days} onValue={(n) => set("general", "work_retention_days", n)} className="w-28 tabular" />
          </Field>
          <Field label="MinerU tier" hint="standard 較慢，公式與表格略好" htmlFor="set-tier">
            <Select value={edited.engines.mineru_tier} onValueChange={(v) => set("engines", "mineru_tier", v as Settings["engines"]["mineru_tier"])}>
              <SelectTrigger id="set-tier" className="w-44 font-mono">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="basic">basic</SelectItem>
                <SelectItem value="standard">standard</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field label="Docling OCR" hint="掃描檔用的文字辨識引擎" htmlFor="set-ocr">
            <Select value={edited.engines.docling_ocr} onValueChange={(v) => set("engines", "docling_ocr", v as Settings["engines"]["docling_ocr"])}>
              <SelectTrigger id="set-ocr" className="w-44 font-mono">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="rapidocr">rapidocr</SelectItem>
                <SelectItem value="easyocr">easyocr</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field label="上傳上限" hint="單一檔案，MB" htmlFor="set-upload">
            <div className="flex items-center gap-2">
              <NumberField id="set-upload" min={1} max={1024 * 1024} value={Math.round(edited.limits.upload_max_bytes / MB)} onValue={(n) => set("limits", "upload_max_bytes", n * MB)} className="w-28 tabular" />
              <span className="text-xs text-muted-foreground">MB</span>
            </div>
          </Field>
          <Field label="磁碟空間倍數" hint="送出前要求的可用空間 = 輸入大小 × 倍數" htmlFor="set-disk">
            <NumberField id="set-disk" min={1} max={100000} value={edited.limits.disk_space_factor} onValue={(n) => set("limits", "disk_space_factor", n)} className="w-28 tabular" />
          </Field>
          <Field label="允許線上音訊轉錄" hint="開啟後，音訊檔會送到 Google 轉成文字" htmlFor="set-audio">
            <Switch id="set-audio" checked={edited.general.enable_audio} onCheckedChange={(v) => set("general", "enable_audio", v)} />
          </Field>
        </div>

        <div className="border-t bg-muted/30 px-4 py-3">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <Lock className="size-3.5" /> 伺服器
            </span>
            <span>
              host <span className="font-mono text-foreground">{original.server.host}</span>
            </span>
            <span>
              port <span className="font-mono text-foreground">{original.server.port}</span>
            </span>
            <span>
              token <span className="font-mono text-foreground">{original.server.token ? "已設定" : "未設定"}</span>
            </span>
            <span>需重新啟動 aidoc serve 才會改變，請在啟動參數或 aidoc.toml 設定。</span>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 border-t px-4 py-3">
          {dirty && <span className="mr-auto text-xs text-muted-foreground">有未儲存的變更</span>}
          <Button type="button" variant="ghost" size="sm" disabled={!dirty} onClick={() => setEdited(original)}>
            <RotateCcw /> 還原
          </Button>
          <Button type="submit" size="sm" disabled={!dirty || update.isPending}>
            {update.isPending ? "儲存中…" : "儲存"}
          </Button>
        </div>
      </form>
    </Panel>
  );
}

type McpSettings = Settings["mcp"];

function McpSettingsForm({ original }: { original: Settings }) {
  const [edited, setEdited] = useState<Settings>(original);
  const update = useUpdateSettings();
  useEffect(() => setEdited(original), [original]);
  const patch = diffSettings(original, edited);
  const mcpPatch = patch.mcp ? { mcp: patch.mcp } : {};
  const dirty = Object.keys(mcpPatch).length > 0;
  const m = edited.mcp;
  const set = <K extends keyof McpSettings>(key: K, value: McpSettings[K]) => setEdited((e) => ({ ...e, mcp: { ...e.mcp, [key]: value } }));
  const num = (key: keyof McpSettings, id: string, min: number, max: number, unit?: string) => (
    <div className="flex items-center gap-2">
      <NumberField id={id} min={min} max={max} value={m[key] as number} onValue={(n) => set(key, n as never)} className="w-32 tabular" />
      {unit && <span className="text-xs text-muted-foreground">{unit}</span>}
    </div>
  );
  const save = () =>
    update.mutateAsync(mcpPatch).then(
      () => toast.success("已儲存"),
      (e) => toast.error(describeError(e)),
    );

  return (
    <Panel>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (dirty) void save();
        }}
      >
        <div className="divide-y px-4">
          <Field label="啟用 MCP" hint="關閉後 /mcp 回 404；後台照常可用" htmlFor="mcp-enabled">
            <Switch id="mcp-enabled" checked={m.enabled} onCheckedChange={(v) => set("enabled", v)} />
          </Field>
          <Field label="每分鐘呼叫上限" hint="每把 token 的 tools/call 次數；token 可個別覆寫" htmlFor="mcp-rate">
            {num("rate_limit_per_min", "mcp-rate", 1, 100000, "次")}
          </Field>
          <Field label="回應預算（tokens）" hint="單次回應的上限，超過會截斷並給 next" htmlFor="mcp-budget">
            {num("response_token_budget", "mcp-budget", 500, 25000)}
          </Field>
          <Field label="預設到期天數" hint="建立 token 時預選的到期" htmlFor="mcp-ttl">
            {num("default_token_ttl_days", "mcp-ttl", 1, 3650, "天")}
          </Field>
          <Field label="最長到期天數" htmlFor="mcp-ttl-max">
            {num("max_token_ttl_days", "mcp-ttl-max", 1, 3650, "天")}
          </Field>
          <Field label="允許永不過期" hint="開啟後建立 token 時可選「永不過期」" htmlFor="mcp-noexp">
            <div className="flex flex-wrap items-center gap-3">
              <Switch id="mcp-noexp" checked={m.allow_no_expiry} onCheckedChange={(v) => set("allow_no_expiry", v)} />
              {m.allow_no_expiry && (
                <span className="flex items-center gap-1.5 text-xs text-warn">
                  <TriangleAlert className="size-3.5" /> 不會過期的 token 外洩後只能手動撤銷
                </span>
              )}
            </div>
          </Field>
          <Field label="每把 token 同時工作數" hint="convert / reconvert 進行中的上限" htmlFor="mcp-jobs">
            {num("max_concurrent_jobs_per_token", "mcp-jobs", 1, 100)}
          </Field>
          <Field label="上傳上限（MB）" hint="convert_document 的檔案大小" htmlFor="mcp-upload">
            {num("max_upload_mb", "mcp-upload", 1, 2048, "MB")}
          </Field>
          <Field label="呼叫紀錄保留天數" hint="0 = 只受筆數上限限制" htmlFor="mcp-retention">
            {num("call_log_retention_days", "mcp-retention", 0, 3650, "天")}
          </Field>
          <Field label="呼叫紀錄筆數上限" htmlFor="mcp-rows">
            {num("call_log_max_rows", "mcp-rows", 1000, 10_000_000, "筆")}
          </Field>
          <Field label="額外允許的 Host" hint="一行一個，例如 MagicDNS 名稱；綁定的位址會自動加入" htmlFor="mcp-hosts">
            <ListField id="mcp-hosts" rows={2} value={m.allowed_hosts} onValue={(v) => set("allowed_hosts", v)} className="font-mono text-[12.5px]" />
          </Field>
          <Field label="convert_path 白名單根目錄" hint="必須是主機上存在的絕對路徑；留空即隱藏 convert_path" htmlFor="mcp-roots">
            <ListField id="mcp-roots" rows={2} value={m.local_path_roots} onValue={(v) => set("local_path_roots", v)} className="font-mono text-[12.5px]" />
          </Field>
        </div>
        <div className="flex items-center justify-end gap-2 border-t px-4 py-3">
          {dirty && <span className="mr-auto text-xs text-muted-foreground">有未儲存的變更</span>}
          <Button type="button" variant="ghost" size="sm" disabled={!dirty} onClick={() => setEdited(original)}>
            <RotateCcw /> 還原
          </Button>
          <Button type="submit" size="sm" disabled={!dirty || update.isPending}>
            {update.isPending ? "儲存中…" : "儲存"}
          </Button>
        </div>
      </form>
    </Panel>
  );
}

function TokenCard({ highlight }: { highlight?: boolean }) {
  const qc = useQueryClient();
  const { reconnect } = useEventStream();
  const [value, setValue] = useState(getToken());
  const saved = getToken();
  const apply = (t: string) => {
    setToken(t.trim());
    reconnect();
    void qc.invalidateQueries();
    toast.success(t.trim() ? "已儲存 token" : "已清除 token");
  };
  return (
    <Panel className={cn(highlight && "border-warn/40")}>
      <PanelHeader
        title="API token"
        description={
          highlight
            ? "伺服器要求 token（以 --token 啟動）。輸入後這個瀏覽器的所有請求與即時更新都會帶上它。"
            : "只存在這個瀏覽器（localStorage）。連到以 --token 啟動的伺服器時才需要。"
        }
      />
      <form
        className="flex flex-col gap-2 p-4 sm:flex-row"
        onSubmit={(e) => {
          e.preventDefault();
          apply(value);
        }}
      >
        <div className="relative flex-1">
          <KeyRound className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="password"
            aria-label="API token"
            autoComplete="off"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="貼上啟動 aidoc serve 時的 --token"
            className="pl-8 font-mono"
          />
        </div>
        <div className="flex gap-2">
          <Button type="submit" size="sm" className="h-8">
            儲存 token
          </Button>
          {saved && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8"
              onClick={() => {
                setValue("");
                apply("");
              }}
            >
              清除
            </Button>
          )}
        </div>
      </form>
    </Panel>
  );
}
