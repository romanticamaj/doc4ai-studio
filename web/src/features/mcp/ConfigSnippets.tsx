import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import type { McpSnippet } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

/** execCommand("copy") through an off-screen textarea: works on plain-HTTP origins (the tailnet URL), where
 *  `navigator.clipboard` does not exist. Focus and the user's selection are put back afterwards. */
function legacyCopy(text: string): boolean {
  const active = document.activeElement as HTMLElement | null;
  const selection = document.getSelection();
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i)) : [];
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.setAttribute("aria-hidden", "true");
  ta.style.cssText = "position:fixed;top:0;left:-9999px;opacity:0;pointer-events:none";
  document.body.appendChild(ta);
  let ok = false;
  try {
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, text.length);
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  } finally {
    ta.remove();
    if (selection) {
      selection.removeAllRanges();
      for (const r of ranges) selection.addRange(r);
    }
    active?.focus?.();
  }
  return ok;
}

export async function copyText(text: string): Promise<boolean> {
  let ok = false;
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch {
      ok = false;
    }
  }
  if (!ok) ok = legacyCopy(text);
  if (ok) toast.success("已複製");
  else toast.error("無法存取剪貼簿，請手動選取複製");
  return ok;
}

export function CopyButton({ text, label = "複製", className }: { text: string; label?: string; className?: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      className={cn("shrink-0", className)}
      onClick={async () => {
        if (await copyText(text)) {
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        }
      }}
    >
      {done ? <Check /> : <Copy />}
      {label}
    </Button>
  );
}

/** One tab per client (Claude Code / Cursor / VS Code / Claude Desktop), each with its snippet and a copy button. */
export function ConfigSnippets({ snippets, className }: { snippets: McpSnippet[]; className?: string }) {
  if (!snippets.length) return null;
  return (
    <Tabs defaultValue={snippets[0].client} className={cn("min-w-0", className)}>
      <TabsList aria-label="用戶端" className="h-auto max-w-full flex-wrap justify-start">
        {snippets.map((s) => (
          <TabsTrigger key={s.client} value={s.client} className="flex-none text-xs">
            {s.title.replace(/（.*$/, "")}
          </TabsTrigger>
        ))}
      </TabsList>
      {snippets.map((s) => (
        <TabsContent key={s.client} value={s.client} className="min-w-0">
          <div className="flex items-center justify-between gap-2 pb-1.5">
            <span className="min-w-0 truncate text-xs text-muted-foreground">{s.title}</span>
            <CopyButton text={s.text} />
          </div>
          <pre className="max-h-72 overflow-auto rounded-md border bg-muted/50 p-3 font-mono text-[12px] leading-relaxed whitespace-pre">
            {s.text}
          </pre>
        </TabsContent>
      ))}
    </Tabs>
  );
}
