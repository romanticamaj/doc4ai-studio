import { useEffect } from "react";
import { TriangleAlert } from "lucide-react";
import { Input } from "@/components/ui/input";
import { useRootCheck } from "@/api/queries";
import type { SystemInfo } from "@/api/types";
import { cn } from "@/lib/utils";

const SEP = /[\\/]$/;

function join(root: string, sub: string): string {
  if (!root) return "";
  const sep = root.includes("/") && !root.includes("\\") ? "/" : "\\";
  return `${root.replace(SEP, "")}${sep}${sub}`;
}

/**
 * Settings → 「Doc4AI 資料夾」 (spec 2026-10-29 §11.1; unset = the platform default folder, user decision
 * 2026-10-29). Shows where results (out\) and source copies (source\) go, checks the typed value live
 * (POST /api/settings/root-check, 400 ms after typing stops) and tells the form whether it may be saved.
 */
export function RootDirField({
  value,
  saved,
  sys,
  onChange,
  onValidity,
}: {
  value: string;
  /** the value in aidoc.toml (no check needed while the field still shows it) */
  saved: string;
  sys: SystemInfo | undefined;
  onChange: (v: string) => void;
  onValidity: (ok: boolean) => void;
}) {
  const fromEnv = !!sys?.root_from_env;
  const changed = value.trim() !== saved.trim();
  const check = useRootCheck(value.trim(), changed && !fromEnv);
  const problem = changed ? (check.data && !check.data.ok ? check.data.problem : null) : null;
  const pending = changed && (!check.settled || check.isFetching);
  const ok = !changed || (!pending && !!check.data?.ok);
  useEffect(() => {
    onValidity(ok);
  }, [ok, onValidity]);

  const fallback = sys?.root_default ?? "";
  const root = fromEnv ? (sys?.root_dir ?? "") : changed ? (check.data?.root_dir ?? (value.trim() || fallback)) : value.trim() || fallback;
  const out = changed && check.data?.output_dir ? check.data.output_dir : join(root, "out");
  const src = changed && check.data?.source_dir ? check.data.source_dir : join(root, "source");

  return (
    <div className="grid gap-1.5" data-testid="root-dir-field">
      <Input
        id="set-root"
        value={fromEnv ? (sys?.root_dir ?? "") : value}
        readOnly={fromEnv}
        onChange={(e) => onChange(e.target.value)}
        placeholder={fallback}
        aria-invalid={!!problem}
        aria-describedby="set-root-paths"
        className={cn("font-mono text-[12.5px]", fromEnv && "bg-muted text-muted-foreground")}
        spellCheck={false}
      />
      {fromEnv && <p className="text-xs text-muted-foreground">由環境變數 AIDOC_LIBRARY_ROOT 指定</p>}
      {!fromEnv && !value.trim() && (
        <p className="text-xs text-muted-foreground">
          尚未設定：使用預設的 Doc4AI 資料夾（<span className="font-mono">{fallback}</span>）
        </p>
      )}
      {problem ? (
        <p role="alert" className="flex items-start gap-1.5 text-xs text-danger" data-testid="root-dir-problem">
          <TriangleAlert className="mt-px size-3.5 shrink-0" /> {problem}
        </p>
      ) : (
        <dl id="set-root-paths" className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-xs" aria-busy={pending}>
          <dt className="text-muted-foreground">轉換結果</dt>
          <dd className="truncate font-mono" title={out} data-testid="root-out">
            {out}
          </dd>
          <dt className="text-muted-foreground">原始檔備份</dt>
          <dd className="truncate font-mono" title={src} data-testid="root-source">
            {src}
          </dd>
        </dl>
      )}
    </div>
  );
}
