import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { currentTheme, onThemeChange, toggleTheme, type Theme } from "@/lib/theme";

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => currentTheme());
  useEffect(() => onThemeChange(setTheme), []);
  const dark = theme === "dark";
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      title={dark ? "淺色模式" : "深色模式"}
      onClick={() => toggleTheme()}
    >
      {dark ? <Sun strokeWidth={1.75} /> : <Moon strokeWidth={1.75} />}
    </Button>
  );
}
