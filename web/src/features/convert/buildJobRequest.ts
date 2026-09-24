import type { EngineName, JobCreateBody, Lang } from "@/api/types";

export type EngineChoice = "auto" | EngineName;

export type ConvertForm = {
  uploadIds: string[];
  paths: string[];
  engine: EngineChoice;
  lang: Lang;
  force: boolean;
  retryLow: boolean;
  allowOnlineAudio: boolean;
};

/** Explorer's "Copy as path" wraps paths in double quotes; users paste them as-is. */
export function cleanPath(p: string): string {
  const t = p.trim();
  return t.length >= 2 && t.startsWith('"') && t.endsWith('"') ? t.slice(1, -1).trim() : t;
}

export function splitPaths(text: string): string[] {
  return text.split(/\r?\n/).map(cleanPath).filter(Boolean);
}

export function buildJobRequest(f: ConvertForm): JobCreateBody {
  const body: JobCreateBody = {
    inputs: [
      ...f.uploadIds.map((upload_id) => ({ upload_id })),
      ...f.paths.map(cleanPath).filter(Boolean).map((path) => ({ path })),
    ],
    lang: f.lang,
    force: f.force,
    retry_low: f.retryLow,
    allow_online_audio: f.allowOnlineAudio,
  };
  if (f.engine !== "auto") body.engine = f.engine;
  return body;
}
