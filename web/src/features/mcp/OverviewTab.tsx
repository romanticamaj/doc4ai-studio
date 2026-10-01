import { Link } from "react-router";
import { ShieldAlert, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { useConfigSnippets, useMcpStats, useMcpStatus } from "@/api/mcp";
import type { McpStats, McpStatus } from "@/api/types";
import { MCP_PAGE } from "@/components/layout/nav";
import { Panel, PanelHeader } from "@/components/Panel";
import { StatusBadge, Tag } from "@/components/StatusBadge";
import { ErrorState } from "@/components/states";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { ConfigSnippets, CopyButton } from "./ConfigSnippets";
import { Sparkline } from "./Sparkline";

export function OverviewTab() {
  const status = useMcpStatus();
  const stats = useMcpStats("24h");
  const [endpoint, setEndpoint] = useState<string | undefined>(undefined);
  const snippets = useConfigSnippets(endpoint);
  const chosen = endpoint ?? snippets.data?.endpoint_url;

  if (status.isError) return <ErrorState title="無法讀取 MCP 狀態" error={status.error} onRetry={() => status.refetch()} />;
  if (status.isPending)
    return (
      <div className="grid gap-4">
        <Skeleton className="h-36 rounded-[10px]" />
        <Skeleton className="h-24 rounded-[10px]" />
      </div>
    );
  const s = status.data;

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <StatusPanel s={s} chosen={chosen} onChoose={setEndpoint} />
        <Notices s={s} />
      </div>
      <Metrics s={s} stats={stats.data} />
      <Panel>
        <PanelHeader title="連線設定" description="token 的位置先放占位字；建立 token 時會給填好的版本。" />
        <div className="p-4">
          {snippets.isError ? (
            <ErrorState title="無法產生設定" error={snippets.error} onRetry={() => snippets.refetch()} />
          ) : snippets.data ? (
            <ConfigSnippets key={snippets.data.endpoint_url} snippets={snippets.data.snippets} />
          ) : (
            <Skeleton className="h-40" />
          )}
        </div>
      </Panel>
    </div>
  );
}

function StatusPanel({ s, chosen, onChoose }: { s: McpStatus; chosen?: string; onChoose: (u: string) => void }) {
  const many = s.endpoint_urls.length > 1;
  return (
    <Panel>
      <PanelHeader
        title="伺服器"
        actions={<StatusBadge status={s.enabled ? "ok" : "cancelled"}>{s.enabled ? "啟用中" : "已停用"}</StatusBadge>}
      />
      <div className="grid gap-4 p-4">
        <div>
          <p className="text-xs text-muted-foreground">端點{many && "（點選要放進下方設定的位址）"}</p>
          <ul className="mt-1.5 grid gap-1.5">
            {s.endpoint_urls.map((u) => {
              const on = u === chosen;
              return (
                <li
                  key={u}
                  className={cn(
                    "flex min-w-0 items-center gap-2 rounded-md border px-2 py-1",
                    on && many ? "border-primary/50 bg-primary/5" : "bg-muted/30",
                  )}
                >
                  {many ? (
                    <button
                      type="button"
                      aria-pressed={on}
                      onClick={() => onChoose(u)}
                      className="min-w-0 flex-1 truncate text-left font-mono text-[12px] focus-visible:outline-2 focus-visible:outline-ring"
                      title="用這個位址產生連線設定"
                    >
                      {u}
                    </button>
                  ) : (
                    <code className="min-w-0 flex-1 truncate font-mono text-[12px]">{u}</code>
                  )}
                  <CopyButton text={u} className="h-6 px-2 text-xs" />
                </li>
              );
            })}
          </ul>
        </div>
        <dl className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2 text-xs">
          <dt className="text-muted-foreground">協定版本</dt>
          <dd className="flex flex-wrap gap-1">
            {s.protocol_versions.map((v) => (
              <Tag key={v}>{v}</Tag>
            ))}
          </dd>
          <dt className="text-muted-foreground">SDK 版本</dt>
          <dd className="font-mono">{s.sdk_version}</dd>
          <dt className="text-muted-foreground">搜尋索引</dt>
          <dd className="font-mono">{s.tokenizer}</dd>
        </dl>
      </div>
    </Panel>
  );
}

function Notices({ s }: { s: McpStatus }) {
  const items: { tone: "warn" | "danger" | "neutral"; body: React.ReactNode }[] = [];
  if (s.plaintext_http)
    items.push({ tone: "warn", body: "此連線為 HTTP，token 在網路上以明文傳輸；tailnet 由 WireGuard 加密，一般內網則否。" });
  items.push({
    tone: s.local_path_roots.length ? "danger" : "neutral",
    body: s.local_path_roots.length ? (
      <>
        convert:local 白名單：
        {s.local_path_roots.map((r) => (
          <code key={r} className="mx-0.5 font-mono">
            {r}
          </code>
        ))}
      </>
    ) : (
      "convert:local 白名單：未設定（convert_path 隱藏）"
    ),
  });
  if (s.tokens_expiring_soon > 0)
    items.push({
      tone: "warn",
      body: (
        <Link to={`${MCP_PAGE}?tab=tokens`} className="underline underline-offset-2 hover:text-foreground">
          {s.tokens_expiring_soon} 把 token 將在 14 天內到期
        </Link>
      ),
    });
  for (const w of s.config_warnings ?? []) items.push({ tone: "danger", body: <>aidoc.toml：{w}</> });

  return (
    <Panel>
      <PanelHeader title="安全提示" />
      <ul className="grid gap-2 p-4">
        {items.map((it, i) => (
          <li key={i} className="flex items-start gap-2 text-xs leading-relaxed">
            {it.tone === "neutral" ? (
              <ShieldAlert className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
            ) : (
              <TriangleAlert className={cn("mt-0.5 size-3.5 shrink-0", it.tone === "danger" ? "text-danger" : "text-warn")} />
            )}
            <span className="min-w-0 break-words text-pretty">{it.body}</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function Metrics({ s, stats }: { s: McpStatus; stats?: McpStats }) {
  const rate = s.calls_24h ? `${Math.round((s.errors_24h / s.calls_24h) * 100)}%` : "—";
  const p95s = (stats?.tools ?? []).map((t) => t.p95_ms ?? 0);
  const p95 = p95s.length ? `${Math.max(...p95s)} ms` : "—";
  const cells: { label: string; value: string; extra?: React.ReactNode; tone?: string }[] = [
    { label: "活躍 client（5 分鐘）", value: String(s.active_clients) },
    {
      label: "24 小時呼叫",
      value: String(s.calls_24h),
      extra: stats ? <Sparkline values={stats.series.map((b) => b.calls)} label="24 小時呼叫（每小時）" /> : null,
    },
    { label: "錯誤率", value: rate, tone: s.calls_24h && s.errors_24h / s.calls_24h > 0.1 ? "text-danger" : undefined },
    { label: "p95 延遲", value: p95 },
  ];
  return (
    <Panel className="grid grid-cols-2 divide-border md:grid-cols-4 md:divide-x [&>*:nth-child(-n+2)]:border-b md:[&>*:nth-child(-n+2)]:border-b-0">
      {cells.map((c, i) => (
        <div key={c.label} className={cn("flex min-w-0 flex-col gap-1 px-4 py-3", i % 2 === 1 && "border-l md:border-l-0")}>
          <span className="text-xs text-muted-foreground">{c.label}</span>
          <span className="flex items-end justify-between gap-2">
            <span className={cn("text-2xl font-semibold tracking-tight tabular", c.tone)}>{c.value}</span>
            {c.extra}
          </span>
        </div>
      ))}
    </Panel>
  );
}
