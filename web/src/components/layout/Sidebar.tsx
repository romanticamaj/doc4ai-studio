import { NavLink } from "react-router";
import { cn } from "@/lib/utils";
import { BrandMark } from "./BrandMark";
import { NAV } from "./nav";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

type Props = {
  /** "full" = labels (desktop and drawer), "rail" = icons only (tablet widths) */
  variant?: "full" | "rail";
  onNavigate?: () => void;
  footer?: React.ReactNode;
};

export function Sidebar({ variant = "full", onNavigate, footer }: Props) {
  const rail = variant === "rail";
  return (
    <nav aria-label="Main" className="flex h-full flex-col">
      <div className={cn("flex h-12 shrink-0 items-center gap-2.5 px-4", rail && "justify-center px-0")}>
        <BrandMark className="size-[22px]" />
        {!rail && (
          <div className="flex items-baseline gap-1.5">
            <span className="text-[15px] font-semibold tracking-tight">AIDoc</span>
            <span className="text-xs text-muted-foreground">Studio</span>
          </div>
        )}
      </div>
      <ul className={cn("mt-2 flex flex-col gap-0.5 px-2.5", rail && "items-center px-2")}>
        {NAV.map((item) => {
          const link = (
            <NavLink
              to={item.to}
              onClick={onNavigate}
              aria-label={rail ? item.label : undefined}
              className={({ isActive }) =>
                cn(
                  "group flex h-8 items-center gap-2.5 rounded-md px-2.5 text-[13px] text-muted-foreground transition-colors",
                  "hover:bg-sidebar-accent hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring",
                  rail && "w-9 justify-center px-0",
                  isActive && "bg-sidebar-accent font-medium text-foreground",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon
                    className={cn("size-4 shrink-0", isActive ? "text-primary" : "text-muted-foreground group-hover:text-foreground")}
                    strokeWidth={1.75}
                  />
                  {!rail && <span>{item.label}</span>}
                </>
              )}
            </NavLink>
          );
          return (
            <li key={item.to}>
              {rail ? (
                <Tooltip>
                  <TooltipTrigger asChild>{link}</TooltipTrigger>
                  <TooltipContent side="right">
                    {item.label} <span className="text-muted-foreground">· {item.hint}</span>
                  </TooltipContent>
                </Tooltip>
              ) : (
                link
              )}
            </li>
          );
        })}
      </ul>
      <div className="mt-auto">{footer}</div>
    </nav>
  );
}
