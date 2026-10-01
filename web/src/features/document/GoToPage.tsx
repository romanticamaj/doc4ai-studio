import { useState } from "react";
import { Input } from "@/components/ui/input";

/** 「跳至頁」: a page number + Enter scrolls both panes to that page (spec 2026-10-01 §9.4). */
export function GoToPage({ pages, onGo }: { pages: number; onGo: (page: number) => void }) {
  const [text, setText] = useState("");
  return (
    <label className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      跳至頁
      <Input
        type="number"
        inputMode="numeric"
        min={1}
        max={pages}
        aria-label="跳至頁碼"
        placeholder={`1–${pages}`}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key !== "Enter") return;
          const n = Math.trunc(Number(text));
          if (!Number.isFinite(n) || !text.trim()) return;
          const page = Math.min(pages, Math.max(1, n));
          setText(String(page));
          onGo(page);
        }}
        className="h-7 w-20 px-2 text-xs tabular"
      />
    </label>
  );
}
