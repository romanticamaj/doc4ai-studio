import { useSearchParams } from "react-router";
import { Page, PageHeader } from "@/components/layout/Page";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState } from "@/components/states";

const TABS = ["overview", "tokens", "clients", "calls"] as const;
type Tab = (typeof TABS)[number];
const LABEL: Record<Tab, string> = { overview: "總覽", tokens: "Tokens", clients: "連線", calls: "呼叫紀錄" };

export default function McpPage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab: Tab = (TABS as readonly string[]).includes(raw ?? "") ? (raw as Tab) : "overview";
  const select = (v: string) => {
    const next = new URLSearchParams(params);
    if (v === "overview") next.delete("tab");
    else next.set("tab", v);
    setParams(next, { replace: true });
  };
  return (
    <Page wide>
      <PageHeader title="MCP" description="讓其他 AI 透過 MCP 使用這個 Library：發 token、看連線與呼叫。" />
      <Tabs value={tab} onValueChange={select}>
        <TabsList aria-label="MCP 分頁">
          {TABS.map((t) => (
            <TabsTrigger key={t} value={t}>{LABEL[t]}</TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="overview"><EmptyState title="總覽（Task 30）" /></TabsContent>
        <TabsContent value="tokens"><EmptyState title="Tokens（Task 29）" /></TabsContent>
        <TabsContent value="clients"><EmptyState title="連線（Task 31）" /></TabsContent>
        <TabsContent value="calls"><EmptyState title="呼叫紀錄（Task 32）" /></TabsContent>
      </Tabs>
    </Page>
  );
}
