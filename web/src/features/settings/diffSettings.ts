import type { Settings, SettingsPatch } from "@/api/types";

/** Keys set when `aidoc serve` starts; the API refuses changes to them (403 server_readonly / token_readonly). */
const READ_ONLY = new Set<keyof Settings>(["server"]);

/** The changed keys only, grouped by section (PUT /api/settings takes partial sections). */
export function diffSettings(original: Settings, edited: Settings): SettingsPatch {
  const out: Record<string, Record<string, unknown>> = {};
  for (const section of Object.keys(edited) as Array<keyof Settings>) {
    if (READ_ONLY.has(section)) continue;
    const a = (original[section] ?? {}) as Record<string, unknown>;
    const b = edited[section] as Record<string, unknown>;
    for (const key of Object.keys(b)) {
      if (a[key] !== b[key]) (out[section] ??= {})[key] = b[key];
    }
  }
  return out as SettingsPatch;
}
