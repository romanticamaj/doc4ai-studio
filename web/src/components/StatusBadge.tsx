import { cn } from "@/lib/utils";

type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

const TONE: Record<Tone, string> = {
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  danger: "bg-danger-soft text-danger",
  info: "bg-info-soft text-info",
  neutral: "bg-neutral-soft text-muted-foreground",
};

const STATUS_TONE: Record<string, Tone> = {
  done: "ok",
  ok: "ok",
  warn: "warn",
  low: "warn",
  failed: "danger",
  running: "info",
  probing: "info",
  converting: "info",
  checking: "info",
  queued: "neutral",
  cancelled: "neutral",
  skipped: "neutral",
  orphaned: "neutral",
  // MCP call / token / client states
  tool_error: "warn",
  forbidden_scope: "warn",
  auth_error: "danger",
  rate_limited: "danger",
  protocol_error: "danger",
  active: "ok",
  expired: "warn",
  revoked: "neutral",
  idle: "neutral",
  new: "info",
};

const LIVE = new Set(["running", "probing", "converting", "checking"]);

export function toneOf(status: string): Tone {
  return STATUS_TONE[status] ?? "neutral";
}

/** Status pill: the raw status word (it is what the CLI and API print), colour = meaning. */
export function StatusBadge({ status, className, children }: { status: string; className?: string; children?: React.ReactNode }) {
  const tone = toneOf(status);
  return (
    <span
      className={cn(
        "inline-flex h-5 shrink-0 items-center gap-1.5 rounded-full px-2 font-mono text-[11px] font-medium leading-none",
        TONE[tone],
        className,
      )}
    >
      <span className={cn("size-1.5 rounded-full bg-current", LIVE.has(status) && "animate-pulse")} aria-hidden />
      {children ?? status}
    </span>
  );
}

export function Tag({ children, className, mono = true }: { children: React.ReactNode; className?: string; mono?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 shrink-0 items-center rounded-md border px-1.5 text-[11px] leading-none text-muted-foreground",
        mono && "font-mono",
        className,
      )}
    >
      {children}
    </span>
  );
}
