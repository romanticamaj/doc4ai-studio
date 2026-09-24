import { useRef, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { ChevronDown, FolderInput, RotateCcw, TriangleAlert, UploadCloud, X } from "lucide-react";
import { Page, PageHeader } from "@/components/layout/Page";
import { Panel, PanelHeader } from "@/components/Panel";
import { Meter } from "@/components/Meter";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { useCreateJob, useSettings } from "@/api/queries";
import type { Lang } from "@/api/types";
import { buildJobRequest, splitPaths, type EngineChoice } from "@/features/convert/buildJobRequest";
import { fileIcon } from "@/features/convert/fileIcon";
import { useUploads, type UploadItem, type UploadPhase } from "@/upload/useUploads";
import { describeError } from "@/lib/errors";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";

const PHASE_LABEL: Record<UploadPhase, string> = {
  queued: "等待中",
  hashing: "計算檢查碼",
  uploading: "上傳中",
  done: "完成",
  error: "失敗",
};

const ENGINES: Array<{ value: EngineChoice; label: string; hint: string }> = [
  { value: "auto", label: "自動", hint: "依檔案類型選擇，失敗時換備援" },
  { value: "markitdown", label: "markitdown", hint: "Office、HTML、電子 PDF" },
  { value: "docling", label: "docling", hint: "掃描檔與 OCR" },
  { value: "mineru", label: "mineru", hint: "公式與複雜版面" },
];

const LANGS: Array<{ value: Lang; label: string }> = [
  { value: "cht", label: "繁中＋英文" },
  { value: "en", label: "英文" },
];

export default function ConvertPage() {
  const uploads = useUploads();
  const settings = useSettings();
  const createJob = useCreateJob();
  const navigate = useNavigate();

  const [pathsText, setPathsText] = useState("");
  const [engine, setEngine] = useState<EngineChoice>("auto");
  const [lang, setLang] = useState<Lang | null>(null);
  const [force, setForce] = useState(false);
  const [retryLow, setRetryLow] = useState(false);
  const [audio, setAudio] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const effectiveLang: Lang = lang ?? settings.data?.general.lang ?? "cht";
  const paths = splitPaths(pathsText);
  const items = uploads.items;
  const pending = items.filter((i) => i.phase !== "done" && i.phase !== "error").length;
  const failed = items.filter((i) => i.phase === "error").length;
  const ready = items.filter((i) => i.phase === "done" && i.upload_id);
  const inputs = ready.length + paths.length;
  const canSubmit = inputs > 0 && pending === 0 && failed === 0 && !createJob.isPending;

  const blocker =
    inputs === 0 && pending === 0
      ? "加入檔案或輸入路徑後就能開始"
      : pending > 0
        ? `等待 ${pending} 個檔案上傳完成`
        : failed > 0
          ? "有檔案上傳失敗：重試或移除它"
          : null;

  async function submit() {
    try {
      const job = await createJob.mutateAsync(
        buildJobRequest({
          uploadIds: ready.map((i) => i.upload_id!),
          paths,
          engine,
          lang: effectiveLang,
          force,
          retryLow,
          allowOnlineAudio: audio,
        }),
      );
      uploads.clear();
      setPathsText("");
      navigate(`/jobs/${job.id}`);
    } catch (e) {
      toast.error(describeError(e));
    }
  }

  const summary = [
    ENGINES.find((e) => e.value === engine)!.label === "自動" ? "自動選擇引擎" : `只用 ${engine}`,
    LANGS.find((l) => l.value === effectiveLang)!.label,
    force && "強制重轉",
    retryLow && "重轉低品質",
    audio && "線上音訊",
  ].filter(Boolean);

  return (
    <Page>
      <PageHeader
        title="Convert"
        description="把 PDF、Office、圖片與網頁轉成帶頁碼標記的 Markdown，給 AI 與 RAG 使用。"
      />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px] xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex min-w-0 flex-col gap-5">
          <Dropzone onFiles={uploads.add} limit={settings.data?.limits.upload_max_bytes} />

          {items.length > 0 && (
            <Panel aria-label="上傳的檔案">
              <PanelHeader
                title={`檔案 · ${items.length}`}
                description={pending > 0 ? "檔案一個一個處理：先算檢查碼，再分塊上傳；斷線會自動續傳。" : undefined}
                actions={
                  <Button variant="ghost" size="xs" onClick={uploads.clear}>
                    全部移除
                  </Button>
                }
              />
              <ul className="divide-y">
                {items.map((it) => (
                  <FileRow key={it.key} item={it} onCancel={() => uploads.cancel(it.key)} onRetry={() => uploads.retry(it.key)} />
                ))}
              </ul>
            </Panel>
          )}

          <Panel>
            <PanelHeader
              title="本機路徑"
              description="執行 aidoc serve 這台電腦上的檔案或資料夾，一行一個；資料夾會包含子資料夾。"
            />
            <div className="p-4">
              <Label htmlFor="paths" className="sr-only">
                本機資料夾或檔案路徑
              </Label>
              <Textarea
                id="paths"
                aria-label="本機資料夾或檔案路徑"
                value={pathsText}
                onChange={(e) => setPathsText(e.target.value)}
                placeholder={"D:\\reports\\2026\nC:\\Users\\me\\Documents\\spec.pdf"}
                spellCheck={false}
                rows={3}
                className="min-h-20 resize-y font-mono text-[12.5px]"
              />
              {paths.length > 0 && (
                <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
                  <FolderInput className="size-3.5" /> {paths.length} 個路徑
                </p>
              )}
            </div>
          </Panel>
        </div>

        <aside className="lg:sticky lg:top-16 lg:self-start">
          <Panel>
            <PanelHeader title="轉換設定" description={summary.join("、")} />
            <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
              <CollapsibleTrigger asChild>
                <button
                  type="button"
                  className="flex w-full items-center justify-between px-4 py-2.5 text-[13px] font-medium text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
                >
                  進階選項
                  <ChevronDown className={cn("size-4 transition-transform", advancedOpen && "rotate-180")} />
                </button>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <div className="flex flex-col gap-4 border-t px-4 py-4">
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="engine">引擎</Label>
                    <Select value={engine} onValueChange={(v) => setEngine(v as EngineChoice)}>
                      <SelectTrigger id="engine" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {ENGINES.map((e) => (
                          <SelectItem key={e.value} value={e.value}>
                            <span className={e.value === "auto" ? "" : "font-mono"}>{e.label}</span>
                            <span className="text-xs text-muted-foreground">{e.hint}</span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {engine !== "auto" && <p className="text-xs text-muted-foreground">指定引擎時不會自動改用備援。</p>}
                  </div>

                  <fieldset className="flex flex-col gap-1.5">
                    <legend className="mb-1.5 text-sm font-medium">語言</legend>
                    <div role="radiogroup" aria-label="語言" className="grid grid-cols-2 gap-1 rounded-lg bg-muted p-1">
                      {LANGS.map((l) => (
                        <button
                          key={l.value}
                          type="button"
                          role="radio"
                          aria-checked={effectiveLang === l.value}
                          onClick={() => setLang(l.value)}
                          className={cn(
                            "h-7 rounded-md text-[13px] text-muted-foreground transition-colors focus-visible:outline-2 focus-visible:outline-ring",
                            effectiveLang === l.value && "bg-card font-medium text-foreground shadow-panel",
                          )}
                        >
                          {l.label}
                          <span className="ml-1 font-mono text-[11px] opacity-60">{l.value}</span>
                        </button>
                      ))}
                    </div>
                  </fieldset>

                  <Toggle id="force" label="強制重轉" hint="忽略快取，已轉過的檔案也重新轉換" checked={force} onChange={setForce} />
                  <Toggle id="retry-low" label="重轉低品質" hint="快取裡品質為 low 的結果重新轉換" checked={retryLow} onChange={setRetryLow} />
                  <Toggle id="audio" label="允許線上音訊轉錄" hint="音訊檔才會用到" checked={audio} onChange={setAudio} />
                  {audio && (
                    <p className="-mt-2 flex items-start gap-1.5 rounded-md bg-warn-soft px-2.5 py-2 text-xs text-warn">
                      <TriangleAlert className="mt-px size-3.5 shrink-0" />
                      音訊會送到 Google 的線上服務轉成文字。
                    </p>
                  )}
                </div>
              </CollapsibleContent>
            </Collapsible>
            <div className="border-t p-4">
              <Button className="h-9 w-full" disabled={!canSubmit} onClick={submit}>
                {createJob.isPending ? "建立工作中…" : "開始轉換"}
              </Button>
              <p className="mt-2 min-h-4 text-center text-xs text-muted-foreground" aria-live="polite">
                {blocker ?? `${ready.length ? `${ready.length} 個檔案` : ""}${ready.length && paths.length ? "、" : ""}${paths.length ? `${paths.length} 個路徑` : ""}`}
              </p>
            </div>
          </Panel>
        </aside>
      </div>
    </Page>
  );
}

function Toggle({ id, label, hint, checked, onChange }: { id: string; label: string; hint: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <Label htmlFor={id}>{label}</Label>
        <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>
      </div>
      <Switch id={id} checked={checked} onCheckedChange={onChange} className="mt-0.5" />
    </div>
  );
}

function Dropzone({ onFiles, limit }: { onFiles: (files: File[]) => void; limit?: number }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const depth = useRef(0);
  return (
    <div
      data-testid="dropzone"
      onDragEnter={(e) => {
        e.preventDefault();
        depth.current += 1;
        setOver(true);
      }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={() => {
        depth.current = Math.max(0, depth.current - 1);
        if (depth.current === 0) setOver(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        depth.current = 0;
        setOver(false);
        const files = Array.from(e.dataTransfer?.files ?? []);
        if (files.length) onFiles(files);
      }}
      className={cn(
        "group relative rounded-[10px] border border-dashed bg-card transition-colors",
        over ? "border-primary bg-info-soft" : "hover:border-foreground/25",
      )}
    >
      <button
        type="button"
        onClick={() => input.current?.click()}
        className="flex w-full flex-col items-center gap-3 rounded-[10px] px-6 py-10 text-center focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring sm:py-12"
      >
        <span
          className={cn(
            "flex size-11 items-center justify-center rounded-xl border bg-background shadow-panel transition-colors",
            over && "border-primary/40 text-primary",
          )}
        >
          <UploadCloud className="size-5" strokeWidth={1.75} />
        </span>
        <span className="text-[15px] font-medium">
          {over ? "放開以加入檔案" : (
            <>
              拖放檔案到這裡，或<span className="text-primary">選擇檔案</span>
            </>
          )}
        </span>
        <span className="text-xs text-muted-foreground">
          PDF、Word、PowerPoint、Excel、圖片、HTML{limit ? `，單檔上限 ${formatBytes(limit, 0)}` : ""}
        </span>
      </button>
      <input
        ref={input}
        type="file"
        multiple
        className="sr-only"
        tabIndex={-1}
        aria-label="選擇要上傳的檔案"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length) onFiles(files);
          e.target.value = "";
        }}
      />
    </div>
  );
}

function FileRow({ item, onCancel, onRetry }: { item: UploadItem; onCancel: () => void; onRetry: () => void }) {
  const Icon = fileIcon(item.file.name);
  const pct = Math.round(item.progress * 100);
  const active = item.phase === "hashing" || item.phase === "uploading";
  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <span className="flex size-8 shrink-0 items-center justify-center rounded-md border bg-background text-muted-foreground">
        <Icon className="size-4" strokeWidth={1.75} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          <p className="truncate text-[13px] font-medium" title={item.file.name}>
            {item.file.name}
          </p>
          <span
            className={cn(
              "shrink-0 text-xs tabular",
              item.phase === "done" ? "text-ok" : item.phase === "error" ? "text-danger" : "text-muted-foreground",
            )}
          >
            <span>{PHASE_LABEL[item.phase]}</span>
            {active && <span className="ml-1.5">{pct}%</span>}
          </span>
        </div>
        <div className="mt-1.5 flex items-center gap-3">
          {item.phase === "error" ? (
            <p className="truncate text-xs text-danger">{describeUploadError(item)}</p>
          ) : (
            <Meter
              value={item.phase === "done" ? 100 : active ? pct : 0}
              tone={item.phase === "done" ? "ok" : "info"}
              label={`${item.file.name} ${PHASE_LABEL[item.phase]}`}
              className="h-1"
            />
          )}
          <span className="shrink-0 text-xs text-muted-foreground tabular">{formatBytes(item.file.size)}</span>
        </div>
      </div>
      <div className="flex shrink-0 items-center">
        {item.phase === "error" && (
          <Button variant="ghost" size="icon-sm" aria-label={`重試 ${item.file.name}`} title="重試" onClick={onRetry}>
            <RotateCcw />
          </Button>
        )}
        <Button variant="ghost" size="icon-sm" aria-label={`移除 ${item.file.name}`} title="移除" onClick={onCancel}>
          <X />
        </Button>
      </div>
    </li>
  );
}

function describeUploadError(item: UploadItem): string {
  const b = item.errorBody;
  if (b?.error === "upload_too_large") return `超過上傳上限 ${formatBytes(b.limit as number)}`;
  if (b?.error === "insufficient_disk") return `磁碟空間不足：需要 ${formatBytes(b.needed as number)}，剩餘 ${formatBytes(b.free as number)}`;
  if (b?.error === "sha_mismatch") return "檢查碼不符，檔案可能在上傳中被修改";
  if (b?.error === "network_error") return "連線中斷太久，請重試";
  return item.error ?? "上傳失敗";
}
