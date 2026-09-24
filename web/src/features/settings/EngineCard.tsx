import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ChevronDown, Download, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/Panel";
import { StatusBadge } from "@/components/StatusBadge";
import { useSetupEngine } from "@/api/queries";
import type { EngineInfo, EngineName } from "@/api/types";
import { useEventStream } from "@/events/EventStreamProvider";
import { LogPanel } from "@/features/jobs/LogPanel";
import { describeError } from "@/lib/errors";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const ABOUT: Record<EngineName, { what: string; gpu: boolean }> = {
  markitdown: { what: "Office、HTML 與電子 PDF", gpu: false },
  docling: { what: "掃描 PDF 與圖片的 OCR", gpu: true },
  mineru: { what: "公式與複雜版面", gpu: true },
};

function checkedAt(v: EngineInfo["checked_at"]): string | null {
  if (v == null) return null;
  if (typeof v === "number") return formatDateTime(v);
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? String(v) : formatDateTime(d.getTime() / 1000);
}

export function engineState(info: EngineInfo | undefined): { label: string; status: string } {
  if (!info?.installed) return { label: "未安裝", status: "queued" };
  if (!info.ready) return { label: "已安裝未檢查", status: "low" };
  return { label: "就緒", status: "done" };
}

export function EngineCard({ name, info }: { name: EngineName; info: EngineInfo | undefined }) {
  const setup = useSetupEngine();
  const { subscribe } = useEventStream();
  const [installing, setInstalling] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const state = engineState(info);
  const about = ABOUT[name];

  useEffect(
    () =>
      subscribe((ev) => {
        if (ev.kind !== "setup.done") return;
        const p = ev.payload as { engine: string; ok: boolean; error?: string };
        if (p.engine !== name && p.engine !== "all") return;
        setInstalling(false);
        if (p.engine !== name) return;
        if (p.ok) toast.success(`${name} 已就緒`);
        else toast.error(`${name} 安裝失敗：${p.error ?? "看安裝 log"}`);
      }),
    [subscribe, name],
  );

  const install = () =>
    setup.mutateAsync(name).then(
      () => {
        setInstalling(true);
        setLogOpen(true);
      },
      (e) => toast.error(describeError(e)),
    );

  const details = [
    info?.version && ["版本", info.version],
    info?.torch && ["torch", info.torch],
    info?.cuda != null && ["cuda", String(info.cuda)],
    info?.device && ["device", info.device],
    checkedAt(info?.checked_at) && ["檢查時間", checkedAt(info?.checked_at)],
  ].filter(Boolean) as Array<[string, string]>;

  return (
    <Panel className="flex flex-col">
      <div className="flex items-start justify-between gap-3 px-4 pt-4">
        <div className="min-w-0">
          <h3 className="font-mono text-[14px] font-semibold">{name}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {about.what} · {about.gpu ? "GPU" : "CPU"}
          </p>
        </div>
        {installing ? (
          <StatusBadge status="running">安裝中</StatusBadge>
        ) : (
          <StatusBadge status={state.status}>{state.label}</StatusBadge>
        )}
      </div>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 px-4 text-xs">
        {details.length ? (
          details.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="truncate font-mono" title={v}>
                {v}
              </dd>
            </div>
          ))
        ) : (
          <dd className="col-span-2 text-muted-foreground">
            {info?.installed ? "尚未做自我檢查。" : "安裝會建立獨立的 Python 環境並下載模型。"}
          </dd>
        )}
      </dl>
      <div className="mt-auto flex items-center gap-2 px-4 pt-4 pb-3">
        <Button size="sm" variant={info?.ready ? "outline" : "default"} onClick={install} disabled={installing || setup.isPending}>
          {installing ? <Loader2 className="animate-spin" /> : <Download />}
          {info?.ready ? "重新安裝" : "安裝"}
        </Button>
        <button
          type="button"
          onClick={() => setLogOpen((o) => !o)}
          aria-expanded={logOpen}
          className="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
        >
          安裝 log <ChevronDown className={cn("size-3.5 transition-transform", logOpen && "rotate-180")} />
        </button>
      </div>
      {logOpen && (
        <div className="border-t p-3">
          <LogPanel logKey={`setup:${name}`} className="shadow-none" empty="按「安裝」後，uv sync、模型下載與自我檢查的輸出會即時出現在這裡。" />
        </div>
      )}
    </Panel>
  );
}
