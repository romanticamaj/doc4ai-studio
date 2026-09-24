export type Theme = "light" | "dark";
const KEY = "aidoc_theme";

function stored(): Theme | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

/** The saved choice first, else the OS preference. */
export function getInitialTheme(): Theme {
  const s = stored();
  if (s) return s;
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function currentTheme(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

const listeners = new Set<(t: Theme) => void>();
export function onThemeChange(fn: (t: Theme) => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function applyTheme(t: Theme, persist = false): void {
  document.documentElement.classList.toggle("dark", t === "dark");
  if (persist) {
    try {
      localStorage.setItem(KEY, t);
    } catch {
      /* private mode: the choice is just not remembered */
    }
  }
  listeners.forEach((fn) => fn(t));
}

export function toggleTheme(): Theme {
  const next: Theme = currentTheme() === "dark" ? "light" : "dark";
  applyTheme(next, true);
  return next;
}
