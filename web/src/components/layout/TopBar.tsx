import { Menu } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "./ThemeToggle";
import { BrandMark } from "./BrandMark";

type Props = { onOpenMenu?: () => void; status?: React.ReactNode };

export function TopBar({ onOpenMenu, status }: Props) {
  return (
    <header className="sticky top-0 z-30 flex h-12 shrink-0 items-center gap-2 border-b bg-background/85 px-3 backdrop-blur supports-[backdrop-filter]:bg-background/70 sm:px-4">
      <Button variant="ghost" size="icon-sm" className="md:hidden" aria-label="Open menu" onClick={onOpenMenu}>
        <Menu strokeWidth={1.75} />
      </Button>
      <BrandMark className="size-5 md:hidden" />
      <div className="ml-auto flex min-w-0 items-center gap-1.5 sm:gap-3">
        {status}
        <ThemeToggle />
      </div>
    </header>
  );
}
