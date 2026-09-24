import { useState } from "react";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Sidebar } from "./Sidebar";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { TopBar } from "./TopBar";

type Props = { children: React.ReactNode; status?: React.ReactNode; sidebarFooter?: React.ReactNode };

/**
 * ≥1024px: full sidebar with labels · 768–1023px: icon rail · <768px: drawer from the top-bar menu button.
 */
export function AppShell({ children, status, sidebarFooter }: Props) {
  const [open, setOpen] = useState(false);
  const lg = useMediaQuery("(min-width: 1024px)");
  const md = useMediaQuery("(min-width: 768px)");
  return (
    <TooltipProvider delayDuration={250}>
      <div className="flex min-h-svh bg-background">
        {md && (
          <aside className={lg ? "sticky top-0 h-svh w-56 shrink-0 border-r bg-sidebar" : "sticky top-0 h-svh w-14 shrink-0 border-r bg-sidebar"}>
            <Sidebar variant={lg ? "full" : "rail"} footer={lg ? sidebarFooter : undefined} />
          </aside>
        )}
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetContent side="left" className="w-64 bg-sidebar p-0" aria-describedby={undefined}>
            <SheetTitle className="sr-only">Navigation</SheetTitle>
            <Sidebar onNavigate={() => setOpen(false)} footer={sidebarFooter} />
          </SheetContent>
        </Sheet>
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar onOpenMenu={() => setOpen(true)} status={status} />
          <main className="min-w-0 flex-1">{children}</main>
        </div>
      </div>
    </TooltipProvider>
  );
}
