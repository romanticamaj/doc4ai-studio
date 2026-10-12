import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Panel, PanelHeader } from "@/components/Panel";
import { useUpdateSettings, useVlmCheck, useVlmUsage } from "@/api/queries";
import type { EngineInfo, VlmSettings } from "@/api/types";
import { useEventStream } from "@/events/EventStreamProvider";
import { LogPanel } from "@/features/jobs/LogPanel";
import { describeError } from "@/lib/errors";
import { VlmBackendForm } from "./VlmBackendForm";

/** Settings → VLM OCR (VLM spec §10.2): backend form, remote consent, connection test with its log, usage. */
export function VlmSettingsCard({ vlm, info }: { vlm: VlmSettings; info: EngineInfo | undefined }) {
  const update = useUpdateSettings();
  const check = useVlmCheck();
  const usage = useVlmUsage(30, vlm.backend === "openai");
  const { subscribe } = useEventStream();
  const [testing, setTesting] = useState(false);
  const [logOpen, setLogOpen] = useState(false);

  useEffect(
    () =>
      subscribe((ev) => {
        if (ev.kind !== "setup.done") return;
        const p = ev.payload as { engine: string; ok: boolean; error?: string; action?: string };
        if (p.engine !== "vlm") return;
        setTesting(false);
        if (p.ok) toast.success("VLM 端點測試成功");
        else toast.error(`VLM 端點測試失敗：${p.error ?? "看測試 log"}`);
      }),
    [subscribe],
  );

  return (
    <Panel>
      <PanelHeader
        title="VLM OCR"
        description="所有 PDF／圖片路由的最後一道 fallback，以及逐頁修復的最後一個引擎。預設不使用；頁面影像只有在你同意後才會送到端點。"
      />
      <VlmBackendForm
        settings={vlm}
        info={info}
        saving={update.isPending}
        testing={testing || check.isPending}
        usage={usage.data}
        onSave={(patch) =>
          update.mutateAsync(patch).then(
            () => toast.success("已儲存"),
            (e) => toast.error(describeError(e)),
          )
        }
        onTest={() =>
          check.mutateAsync().then(
            () => {
              setTesting(true);
              setLogOpen(true);
            },
            (e) => toast.error(describeError(e)),
          )
        }
        log={
          logOpen ? (
            <div className="border-t p-3">
              <LogPanel logKey="setup:vlm" className="shadow-none" empty="測試連線的輸出會出現在這裡。" />
            </div>
          ) : null
        }
      />
    </Panel>
  );
}
