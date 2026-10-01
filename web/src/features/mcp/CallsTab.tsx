import { Fragment, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ChevronDown, ChevronRight, ScrollText } from "lucide-react";
import { useMcpCalls, useMcpClients, useMcpStats, type CallFilters } from "@/api/mcp";
import type { McpCall } from "@/api/types";
import { Panel, PanelHeader } from "@/components/Panel";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useEventStream } from "@/events/EventStreamProvider";
import { formatBytes, formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { clientLabel } from "./clientState";
import { TokenSelect } from "./ClientsTab";
import { filtersFromParams, paramsFromFilters, RANGE_OPTIONS, STATUS_OPTIONS } from "./callFilters";

const ALL = "all";
const TOOLS = ["search_library", "list_documents", "get_document_info", "read_document", "get_chunks", "get_job", "convert_document",
  "convert_path", "cancel_job", "reconvert_document"];

function FilterSelect({ label, value, options, onChange, allLabel }: {
  label: string; value: string; options: { value: string; label: string }[]; onChange: (v: string) => void; allLabel: string;
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger size="sm" aria-label={label} className="w-40 max-w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{allLabel}</SelectItem>
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function CallsTab() {
  const [params, setParams] = useSearchParams();
  const filters = filtersFromParams(params);
  const set = (patch: Partial<CallFilters>) => setParams(paramsFromFilters({ ...filters, ...patch }, params), { replace: true });
  const pick = (v: string) => (v === ALL ? undefined : v);

  const calls = useMcpCalls(filters);
  const clients = useMcpClients({});
  const [statsWindow, setStatsWindow] = useState<"24h" | "7d">("24h");
  const stats = useMcpStats(statsWindow);
  const { connected } = useEventStream();
  const [openId, setOpenId] = useState<number | null>(null);

  const toolNames = Array.from(new Set([...TOOLS, ...(stats.data?.tools ?? []).map((t) => t.tool)]));
  const nowS = Date.now() / 1000;
  const since = filters.since ? Number(filters.since) : null;
  const activeRange = since == null ? 0 : RANGE_OPTIONS.filter((r) => r.s > 0).reduce((best, r) =>
    Math.abs(nowS - since - r.s) < Math.abs(nowS - since - best.s) ? r : best, RANGE_OPTIONS[0]).s;

  const rows = calls.data?.pages.flatMap((p) => p.calls) ?? [];

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <TokenSelect value={filters.token_id ?? ALL} onChange={(v) => set({ token_id: pick(v) })} />
        <FilterSelect
          label="篩選 client"
          allLabel="全部 client"
          value={filters.client_id ?? ALL}
          options={(clients.data?.clients ?? []).map((c) => ({ value: c.id, label: `${clientLabel(c)}${c.token_name ? `（${c.token_name}）` : ""}` }))}
          onChange={(v) => set({ client_id: pick(v) })}
        />
        <FilterSelect label="篩選 tool" allLabel="全部 tool" value={filters.tool ?? ALL}
          options={toolNames.map((t) => ({ value: t, label: t }))} onChange={(v) => set({ tool: pick(v) })} />
        <FilterSelect label="篩選狀態" allLabel="全部狀態" value={filters.status ?? ALL}
          options={STATUS_OPTIONS.map((s) => ({ value: s, label: s }))} onChange={(v) => set({ status: pick(v) })} />
        <div role="radiogroup" aria-label="時間範圍" className="flex rounded-lg border p-0.5">
          {RANGE_OPTIONS.map((r) => (
            <button
              key={r.label}
              type="button"
              role="radio"
              aria-checked={activeRange === r.s}
              onClick={() => set({ since: r.s ? String(Math.floor(Date.now() / 1000 - r.s)) : undefined, until: undefined })}
              className={cn(
                "h-6 rounded-md px-2 text-xs focus-visible:outline-2 focus-visible:outline-ring",
                activeRange === r.s ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {r.label}
            </button>
          ))}
        </div>
        <span className="ml-auto inline-flex items-center gap-1.5 text-xs text-muted-foreground" aria-live="polite">
          <span className={cn("size-1.5 rounded-full", connected ? "animate-pulse bg-ok" : "bg-muted-foreground/50")} aria-hidden />
          {connected ? "即時" : "未連線（重新整理以更新）"}
        </span>
      </div>

      <Panel>
        <PanelHeader title="呼叫紀錄" description="每一個 /mcp 請求一筆，含驗證失敗與被限流的；點一列看參數摘要與錯誤。" />
        {calls.isError ? (
          <div className="p-4">
            <ErrorState title="無法讀取呼叫紀錄" error={calls.error} onRetry={() => calls.refetch()} />
          </div>
        ) : calls.isPending ? (
          <div className="grid gap-2 p-4">
            <Skeleton className="h-8" />
            <Skeleton className="h-8" />
            <Skeleton className="h-8" />
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table aria-label="呼叫紀錄" className="w-full min-w-[860px] text-[13px]">
                <thead className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="w-6 py-2 pl-3" aria-hidden />
                    <th className="px-3 py-2 font-medium">時間</th>
                    <th className="px-3 py-2 font-medium">token</th>
                    <th className="px-3 py-2 font-medium">client</th>
                    <th className="px-3 py-2 font-medium">method / tool</th>
                    <th className="px-3 py-2 font-medium">狀態</th>
                    <th className="px-3 py-2 text-right font-medium">延遲</th>
                    <th className="px-3 py-2 text-right font-medium">回應</th>
                    <th className="px-3 py-2 font-medium">工作</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={9} className="p-4">
                        <EmptyState icon={ScrollText} title="沒有符合的呼叫" description="用戶端呼叫 /mcp 後，紀錄會即時出現在這裡。" className="py-8" />
                      </td>
                    </tr>
                  )}
                  {rows.map((c) => (
                    <CallRow key={c.id} c={c} open={openId === c.id} onToggle={() => setOpenId(openId === c.id ? null : c.id)} />
                  ))}
                </tbody>
              </table>
            </div>
            {calls.hasNextPage && (
              <div className="border-t p-3 text-center">
                <Button size="sm" variant="outline" disabled={calls.isFetchingNextPage} onClick={() => calls.fetchNextPage()}>
                  載入更多
                </Button>
              </div>
            )}
          </>
        )}
      </Panel>

      <Panel>
        <PanelHeader
          title="每個 tool"
          description="呼叫數、錯誤率與延遲；只算 tools/call。"
          actions={
            <div role="radiogroup" aria-label="統計範圍" className="flex rounded-lg border p-0.5">
              {(["24h", "7d"] as const).map((w) => (
                <button
                  key={w}
                  type="button"
                  role="radio"
                  aria-checked={statsWindow === w}
                  onClick={() => setStatsWindow(w)}
                  className={cn("h-6 rounded-md px-2 text-xs", statsWindow === w ? "bg-muted font-medium" : "text-muted-foreground hover:text-foreground")}
                >
                  {w === "24h" ? "24 小時" : "7 天"}
                </button>
              ))}
            </div>
          }
        />
        {stats.isError ? (
          <div className="p-4">
            <ErrorState title="無法讀取統計" error={stats.error} onRetry={() => stats.refetch()} />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table aria-label="每個 tool 的統計" className="w-full min-w-[560px] text-[13px]">
              <thead className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 font-medium">tool</th>
                  <th className="px-3 py-2 text-right font-medium">呼叫數</th>
                  <th className="px-3 py-2 text-right font-medium">錯誤率</th>
                  <th className="px-3 py-2 text-right font-medium">p50（ms）</th>
                  <th className="px-3 py-2 text-right font-medium">p95（ms）</th>
                  <th className="px-3 py-2 text-right font-medium">回應 token 中位數</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {(stats.data?.tools ?? []).length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-6 text-center text-muted-foreground">
                      {stats.isPending ? "載入中…" : "這段時間沒有 tool 呼叫"}
                    </td>
                  </tr>
                )}
                {(stats.data?.tools ?? []).map((t) => (
                  <tr key={t.tool}>
                    <td className="px-4 py-2 font-mono text-xs">{t.tool}</td>
                    <td className="px-3 py-2 text-right tabular">{t.calls}</td>
                    <td className={cn("px-3 py-2 text-right tabular", t.error_rate > 0.1 && "text-danger")}>{Math.round(t.error_rate * 100)}%</td>
                    <td className="px-3 py-2 text-right tabular">{t.p50_ms ?? "—"}</td>
                    <td className="px-3 py-2 text-right tabular">{t.p95_ms ?? "—"}</td>
                    <td className="px-3 py-2 text-right tabular">{t.tokens_median ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}

function CallRow({ c, open, onToggle }: { c: McpCall; open: boolean; onToggle: () => void }) {
  const failed = c.status !== "ok";
  return (
    <Fragment>
      <tr
        className={cn("cursor-pointer hover:bg-muted/40", open && "bg-muted/40")}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
        tabIndex={0}
        aria-expanded={open}
      >
        <td className="py-2 pl-3 text-muted-foreground">{open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}</td>
        <td className="px-3 py-2 text-xs whitespace-nowrap tabular">{formatDateTime(c.ts).slice(5)}<span className="text-muted-foreground">:{String(new Date(c.ts * 1000).getSeconds()).padStart(2, "0")}</span></td>
        <td className="max-w-[10rem] truncate px-3 py-2 text-xs">
          {c.token_name ?? (c.token_prefix_seen ? <span className="font-mono text-muted-foreground">{c.token_prefix_seen}…</span> : <span className="text-muted-foreground">—</span>)}
        </td>
        <td className="max-w-[10rem] truncate px-3 py-2 text-xs">{c.client_name ?? <span className="text-muted-foreground">—</span>}</td>
        <td className="px-3 py-2 font-mono text-xs">
          {c.tool_name ? (
            <span>{c.tool_name}</span>
          ) : (
            <span className="text-muted-foreground">{c.method ?? (c.resource_uri ? "resources/read" : "—")}</span>
          )}
        </td>
        <td className="px-3 py-2 whitespace-nowrap">
          <StatusBadge status={c.status} />
          {failed && c.error_code && <span className="ml-1.5 font-mono text-[11px] text-muted-foreground">{c.error_code}</span>}
        </td>
        <td className="px-3 py-2 text-right text-xs tabular">{c.duration_ms != null ? `${c.duration_ms} ms` : "—"}</td>
        <td className="px-3 py-2 text-right text-xs whitespace-nowrap tabular">
          {formatBytes(c.response_bytes)}
          {c.response_tokens_est != null && <span className="block text-muted-foreground">{c.response_tokens_est} tok</span>}
        </td>
        <td className="px-3 py-2 text-xs">
          {c.job_id ? (
            <Link to={`/jobs/${c.job_id}`} onClick={(e) => e.stopPropagation()} className="font-mono text-primary hover:underline">
              {c.job_id.slice(0, 8)}
            </Link>
          ) : null}
        </td>
      </tr>
      {open && (
        <tr className="bg-muted/20">
          <td />
          <td colSpan={8} className="px-3 pt-1 pb-3">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
              <dt className="text-muted-foreground">method</dt>
              <dd className="font-mono">{c.method ?? "—"}</dd>
              {c.resource_uri && (
                <>
                  <dt className="text-muted-foreground">resource</dt>
                  <dd className="font-mono break-all">{c.resource_uri}</dd>
                </>
              )}
              <dt className="text-muted-foreground">HTTP</dt>
              <dd className="font-mono">{c.http_status ?? "—"}</dd>
              {c.error_code && (
                <>
                  <dt className="text-muted-foreground">錯誤碼</dt>
                  <dd className="font-mono text-danger">{c.error_code}</dd>
                </>
              )}
              <dt className="text-muted-foreground">IP</dt>
              <dd className="font-mono">{c.ip ?? "—"}</dd>
              <dt className="text-muted-foreground">協定</dt>
              <dd className="font-mono">{c.protocol_version ?? "—"}</dd>
            </dl>
            {c.args_summary && (
              <pre className="mt-2 max-h-48 overflow-auto rounded-md border bg-background p-2 font-mono text-[12px] whitespace-pre-wrap break-all">
                {c.args_summary}
              </pre>
            )}
          </td>
        </tr>
      )}
    </Fragment>
  );
}
