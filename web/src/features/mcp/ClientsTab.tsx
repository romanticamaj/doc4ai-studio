import { MonitorSmartphone } from "lucide-react";
import { useEffect, useState } from "react";
import { useMcpClients, useMcpTokens } from "@/api/mcp";
import type { McpClient } from "@/api/types";
import { Panel, PanelHeader } from "@/components/Panel";
import { StatusBadge, Tag } from "@/components/StatusBadge";
import { EmptyState, ErrorState } from "@/components/states";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime, formatRelative } from "@/lib/format";
import { clientLabel, splitClients } from "./clientState";

const ALL = "all";

/** Token filter shared by the clients and calls tabs. */
export function TokenSelect({ value, onChange, label = "篩選 token" }: { value: string; onChange: (v: string) => void; label?: string }) {
  const tokens = useMcpTokens();
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger size="sm" aria-label={label} className="w-44 max-w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>全部 token</SelectItem>
        {(tokens.data ?? []).map((t) => (
          <SelectItem key={t.id} value={t.id}>
            {t.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function ClientsTab() {
  const [token, setToken] = useState(ALL);
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), 30_000); // cards fall into history without an event
    return () => clearInterval(id);
  }, []);
  const clients = useMcpClients(token === ALL ? {} : { token_id: token });

  if (clients.isError) return <ErrorState title="無法讀取 client" error={clients.error} onRetry={() => clients.refetch()} />;
  const { active, history } = splitClients(clients.data?.clients ?? [], now);

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground text-pretty">client 名稱與版本由用戶端自報，只供辨識，不是安全邊界。</p>
        <TokenSelect value={token} onChange={setToken} />
      </div>

      <Panel>
        <PanelHeader title="活躍（5 分鐘內）" description="收到新請求時即時更新。" />
        <div className="p-4">
          {clients.isPending ? (
            <Skeleton className="h-28" />
          ) : active.length === 0 ? (
            <EmptyState icon={MonitorSmartphone} title="目前沒有活躍的 client" description="用戶端送出請求後會出現在這裡。" className="py-8" />
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {active.map((c) => (
                <li key={c.id}>
                  <ClientCard c={c} now={now} />
                </li>
              ))}
            </ul>
          )}
        </div>
      </Panel>

      <Panel>
        <PanelHeader title="歷史" description="超過 5 分鐘沒有請求的 client。" />
        {clients.isPending ? (
          <div className="p-4">
            <Skeleton className="h-16" />
          </div>
        ) : history.length === 0 ? (
          <p className="px-4 py-6 text-center text-[13px] text-muted-foreground">
            {active.length ? "沒有其他 client。" : "還沒有 client 連過"}
          </p>
        ) : (
          <div className="relative overflow-x-auto">
            <table aria-label="歷史 client" className="w-full min-w-[780px] text-[13px]">
              <thead className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 font-medium">client</th>
                  <th className="px-3 py-2 font-medium">token</th>
                  <th className="px-3 py-2 font-medium">協定</th>
                  <th className="px-3 py-2 font-medium">IP</th>
                  <th className="px-3 py-2 font-medium">第一次</th>
                  <th className="px-3 py-2 font-medium">最後</th>
                  <th className="px-3 py-2 text-right font-medium">請求數</th>
                  <th className="px-3 py-2 font-medium">狀態</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {history.map((c) => (
                  <tr key={c.id}>
                    <td className="px-4 py-2 font-medium">{clientLabel(c)}</td>
                    <td className="px-3 py-2 text-xs">{c.token_name ?? "—"}</td>
                    <td className="px-3 py-2 font-mono text-xs">{c.protocol_version ?? "—"}</td>
                    <td className="px-3 py-2 font-mono text-xs">{c.last_ip ?? "—"}</td>
                    <td className="px-3 py-2 text-xs whitespace-nowrap tabular">{formatDateTime(c.first_seen)}</td>
                    <td className="px-3 py-2 text-xs whitespace-nowrap tabular" title={formatDateTime(c.last_seen)}>
                      {formatRelative(c.last_seen, now)}
                    </td>
                    <td className="px-3 py-2 text-right text-xs tabular">{c.request_count}</td>
                    <td className="px-3 py-2">
                      {c.token_status === "revoked" ? (
                        <StatusBadge status="revoked">已撤銷</StatusBadge>
                      ) : c.token_status === "expired" ? (
                        <StatusBadge status="expired">已過期</StatusBadge>
                      ) : (
                        <StatusBadge status="idle">閒置</StatusBadge>
                      )}
                    </td>
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

function ClientCard({ c, now }: { c: McpClient; now: number }) {
  const label = clientLabel(c);
  return (
    <article aria-label={label} className="flex h-full flex-col gap-3 rounded-lg border bg-background p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate text-[13px] font-semibold">{label}</h3>
          <p className="truncate text-xs text-muted-foreground">{c.token_name ? `token：${c.token_name}` : "token 已刪除"}</p>
        </div>
        <StatusBadge status="active">活躍</StatusBadge>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">協定</dt>
        <dd>
          <Tag>{c.protocol_version ?? "—"}</Tag>
        </dd>
        <dt className="text-muted-foreground">IP</dt>
        <dd className="truncate font-mono">{c.last_ip ?? "—"}</dd>
        <dt className="text-muted-foreground">最後請求</dt>
        <dd className="tabular" title={formatDateTime(c.last_seen)}>
          {formatRelative(c.last_seen, now)}
        </dd>
        <dt className="text-muted-foreground">請求數</dt>
        <dd className="tabular">{c.request_count}</dd>
      </dl>
    </article>
  );
}
