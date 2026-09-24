export type LibraryView = "cards" | "table";
export type Filters = { q: string; engine: string; status: string; view: LibraryView };

const ENGINES = new Set(["markitdown", "docling", "mineru"]);
const STATUSES = new Set(["ok", "low", "orphaned"]);

export function parseFilters(search: string): Filters {
  const p = new URLSearchParams(search);
  const engine = p.get("engine") ?? "";
  const status = p.get("status") ?? "";
  return {
    q: p.get("q") ?? "",
    engine: ENGINES.has(engine) ? engine : "",
    status: STATUSES.has(status) ? status : "",
    view: p.get("view") === "table" ? "table" : "cards",
  };
}

/** Filters → "?q=…&engine=…&status=…&view=table" (empty values and the default view are left out). */
export function toSearch(f: Filters): string {
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.engine) p.set("engine", f.engine);
  if (f.status) p.set("status", f.status);
  if (f.view !== "cards") p.set("view", f.view);
  const s = p.toString();
  return s ? `?${s}` : "";
}

export function toQuery(f: Filters): { q?: string; engine?: string; status?: string } {
  const out: { q?: string; engine?: string; status?: string } = {};
  if (f.q) out.q = f.q;
  if (f.engine) out.engine = f.engine;
  if (f.status) out.status = f.status;
  return out;
}
