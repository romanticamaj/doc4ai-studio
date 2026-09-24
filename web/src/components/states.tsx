import { AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function PageFallback() {
  return (
    <div className="flex h-[60vh] items-center justify-center text-muted-foreground">
      <Loader2 className="size-5 animate-spin" />
    </div>
  );
}

/** Empty state: says what is missing and offers the next step. */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: React.ComponentType<{ className?: string; strokeWidth?: number }>;
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center rounded-xl border border-dashed px-6 py-14 text-center", className)}>
      {Icon && (
        <div className="mb-4 flex size-10 items-center justify-center rounded-lg border bg-card shadow-panel">
          <Icon className="size-5 text-muted-foreground" strokeWidth={1.75} />
        </div>
      )}
      <p className="font-medium">{title}</p>
      {description && <p className="mt-1 max-w-sm text-[13px] text-muted-foreground text-pretty">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function ErrorState({ title = "無法載入", error, onRetry }: { title?: string; error?: unknown; onRetry?: () => void }) {
  const msg = error instanceof Error ? error.message : error ? String(error) : undefined;
  return (
    <div role="alert" className="flex items-start gap-3 rounded-xl border border-danger/25 bg-danger-soft px-4 py-3.5">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium">{title}</p>
        {msg && <p className="mt-0.5 break-words font-mono text-xs text-muted-foreground">{msg}</p>}
      </div>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          重試
        </Button>
      )}
    </div>
  );
}
