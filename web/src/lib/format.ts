export function formatBytes(n: number | null | undefined, digits = 1): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 ? 0 : digits)} ${units[i]}`;
}

/** Bytes as GB with one decimal ("4.0"), for the GPU memory readout. */
export function gb(n: number): string {
  return (n / 1e9).toFixed(1);
}

const pad = (n: number) => String(n).padStart(2, "0");

/** Epoch seconds → "2026-09-24 14:03". */
export function formatDateTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Epoch seconds → "3 分鐘前" (falls back to the date after a week). */
export function formatRelative(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "—";
  const s = Math.max(0, now - ts);
  if (s < 45) return "剛剛";
  if (s < 3600) return `${Math.round(s / 60)} 分鐘前`;
  if (s < 86400) return `${Math.round(s / 3600)} 小時前`;
  if (s < 7 * 86400) return `${Math.round(s / 86400)} 天前`;
  return formatDateTime(ts).slice(0, 10);
}

export function formatDuration(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s)) return "—";
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} 秒`;
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  if (m < 60) return `${m} 分 ${r} 秒`;
  return `${Math.floor(m / 60)} 時 ${m % 60} 分`;
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

/** Last path component of a Windows or POSIX path (upload tasks already carry just the name). */
export function baseName(p: string): string {
  const parts = p.split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

export function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i + 1).toLowerCase() : "";
}
