import { useId, useState } from "react";
import { Check, Eye, EyeOff, ShieldCheck, TriangleAlert } from "lucide-react";
import { Link } from "react-router";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { checkPat, PAT_PREFIX, type PatCheck } from "./patToken";

const PROBLEM: Partial<Record<PatCheck, React.ReactNode>> = {
  not_pat: (
    <>
      這不是 MCP token。MCP token 以 <code className="font-mono">{PAT_PREFIX}</code> 開頭，在{" "}
      <Link to="/mcp-admin?tab=tokens" className="underline underline-offset-2">
        Tokens 分頁
      </Link>{" "}
      建立。啟動伺服器用的 API token（aidoc serve --token）只給這個網頁介面使用，不能拿來連 MCP。
    </>
  ),
  bearer: <>只要貼 token 本身，不要加「Bearer 」；設定裡已經有了。</>,
  malformed: <>以 {PAT_PREFIX} 開頭，但長度或字元不對：可能沒有複製完整，請重新複製一次。</>,
  checksum: <>token 的檢查碼對不上：可能不完整或打錯了一個字，請直接從建立時的視窗複製。</>,
};

/**
 * 「貼上你的 MCP token 以填入設定」: the value stays in React state of the Overview tab only. It is never sent to the
 * server, never written to localStorage / sessionStorage, and disappears when the tab or the page is left.
 */
export function TokenFill({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const id = useId();
  const [shown, setShown] = useState(false);
  const check = checkPat(value);
  const problem = PROBLEM[check];
  return (
    <div className="grid gap-1.5">
      <label htmlFor={id} className="text-xs font-medium">
        貼上你的 MCP token 以填入設定
      </label>
      <div className="flex items-center gap-2">
        <Input
          id={id}
          type={shown ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={`${PAT_PREFIX}…`}
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          data-1p-ignore=""
          data-lpignore="true"
          data-bwignore=""
          aria-invalid={problem ? true : undefined}
          aria-describedby={`${id}-hint`}
          className="font-mono text-xs"
        />
        <Button type="button" variant="outline" size="sm" className="shrink-0" onClick={() => setShown((s) => !s)} aria-label={shown ? "隱藏 token" : "顯示 token"} aria-pressed={shown}>
          {shown ? <EyeOff /> : <Eye />}
        </Button>
      </div>
      {problem ? (
        <p role="alert" className="flex items-start gap-1.5 text-xs text-warn">
          <TriangleAlert className="mt-px size-3.5 shrink-0" />
          <span>{problem}</span>
        </p>
      ) : check === "ok" ? (
        <p className="flex items-center gap-1.5 text-xs text-ok">
          <Check className="size-3.5 shrink-0" /> 已填入下方設定；複製按鈕會複製填好的內容。
        </p>
      ) : null}
      <p id={`${id}-hint`} className="flex items-start gap-1.5 text-xs text-muted-foreground">
        <ShieldCheck className="mt-px size-3.5 shrink-0" />
        只在這個瀏覽器分頁的記憶體裡替換文字：不會傳到伺服器，也不會存到瀏覽器或磁碟；關掉分頁就沒了。
      </p>
    </div>
  );
}
