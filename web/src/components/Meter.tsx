import { cn } from "@/lib/utils";

type Tone = "info" | "ok" | "warn" | "danger" | "neutral";
const BAR: Record<Tone, string> = {
  info: "bg-primary",
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
  neutral: "bg-muted-foreground/50",
};

/** Thin progress bar. `value` 0..100; NaN/undefined render as 0 (never "NaN%"). */
export function Meter({
  value,
  tone = "info",
  className,
  label,
  indeterminate,
}: {
  value: number | null | undefined;
  tone?: Tone;
  className?: string;
  label?: string;
  indeterminate?: boolean;
}) {
  const v = Number.isFinite(value) ? Math.max(0, Math.min(100, value as number)) : 0;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : Math.round(v)}
      className={cn("relative h-1.5 w-full overflow-hidden rounded-full bg-muted", className)}
    >
      {indeterminate ? (
        <div className={cn("absolute inset-y-0 w-1/3 animate-[meter-slide_1.4s_ease-in-out_infinite] rounded-full", BAR[tone])} />
      ) : (
        <div className={cn("h-full rounded-full transition-[width] duration-300", BAR[tone])} style={{ width: `${v}%` }} />
      )}
    </div>
  );
}
