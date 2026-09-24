import { cn } from "@/lib/utils";

/** Work surface: white card, hairline border, 10px radius. */
export function Panel({ className, children, ...rest }: React.ComponentProps<"section">) {
  return (
    <section className={cn("rounded-[10px] border bg-card text-card-foreground shadow-panel", className)} {...rest}>
      {children}
    </section>
  );
}

export function PanelHeader({
  title,
  description,
  actions,
  className,
  divider = true,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
  divider?: boolean;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-3 px-4 py-3", divider && "border-b", className)}>
      <div className="min-w-0">
        <h2 className="text-[13px] font-semibold">{title}</h2>
        {description && <p className="mt-0.5 text-xs text-muted-foreground text-pretty">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
    </div>
  );
}
