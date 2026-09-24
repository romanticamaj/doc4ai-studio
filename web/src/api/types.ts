// Mirrors index §8 (API contracts, including "§8 as implemented in P3"). Field names are the API's snake_case.

export type JobStatus = "queued" | "running" | "done" | "cancelled";
export type TaskStatus =
  | "queued"
  | "probing"
  | "converting"
  | "checking"
  | "done"
  | "low"
  | "failed"
  | "skipped"
  | "cancelled";
export type SegmentStatus = "queued" | "converting" | "done" | "failed";
export type DocStatus = "ok" | "low" | "orphaned";
export type EngineName = "markitdown" | "docling" | "mineru";
export type Lang = "cht" | "en";
export type ErrorKind = "transient" | "engine" | "input";

export interface ConvertOptionsJson {
  output_dir: string;
  engine: EngineName | null;
  lang: Lang;
  force: boolean;
  retry_low: boolean;
  timeout_s: number | null;
  allow_online_audio: boolean;
  mineru_tier: string;
  docling_ocr: string;
}

export interface Job {
  id: string;
  created_at: number;
  options: ConvertOptionsJson;
  status: JobStatus;
  origin: "cli" | "web";
  progress: { done: number; total: number };
}

export interface Attempt {
  engine: string;
  attempt: number;
  score: number | null;
  reasons: string[];
  error_kind?: string | null;
  error_msg?: string | null;
}

export interface Quality {
  score: number;
  level: "ok" | "low";
  reasons: string[];
  metrics?: Record<string, unknown>;
}

export interface Segment {
  id: string;
  idx: number;
  page_start: number | null;
  page_end: number | null;
  status: SegmentStatus;
  attempt: number;
}

export interface Task {
  id: string;
  job_id: string;
  source_path: string;
  work_path?: string | null;
  sha256: string;
  size: number;
  mtime?: number;
  lang: Lang;
  engine: string | null;
  tried: Attempt[];
  attempt: number;
  status: TaskStatus;
  error_kind: ErrorKind | null;
  error_msg: string | null;
  quality: Quality | null;
  output_dir: string;
  flags?: { force?: boolean };
  document_id: string | null;
  pages: number | null;
  progress: { pages_done: number; pages_total: number | null };
  created_at: number;
  updated_at: number;
  segments?: Segment[];
}

export interface JobDetail {
  job: Job;
  tasks: Task[];
}

export interface Document {
  id: string;
  sha256: string;
  source_path: string;
  output_dir: string;
  engine: string;
  quality: Quality;
  pages: number | null;
  lang: Lang;
  aidoc_version: string;
  created_at: number;
  status: DocStatus;
  stem: string;
}

export interface Sidecar {
  source?: string;
  sha256?: string;
  pages?: number | null;
  engine?: string;
  tried?: Attempt[];
  segments?: number;
  probe?: Record<string, unknown>;
  quality?: Quality;
  lang?: Lang;
  elapsed_s?: number;
  aidoc_version?: string;
  [k: string]: unknown;
}

export interface DocumentDetail {
  document: Document;
  source_available: boolean;
  output_available: boolean;
  sidecar: Sidecar | null;
}

export interface EngineInfo {
  installed: boolean;
  ready: boolean;
  checked_at?: number | string;
  cuda?: boolean;
  version?: string;
  torch?: string;
  device?: string;
}

export interface QueueInfo {
  paused: boolean;
  length: number;
  running_task_id: string | null;
}

export interface SystemInfo {
  engines: Record<EngineName, EngineInfo>;
  gpu: { name: string; mem_total: number; mem_used: number } | null;
  queue: QueueInfo;
  disk_free: number | null;
  output_dir: string;
  version: string;
  long_paths_enabled: boolean;
}

export interface Settings {
  general: { output_dir: string; lang: Lang; work_retention_days: number; enable_audio: boolean };
  engines: { mineru_tier: "basic" | "standard"; docling_ocr: "easyocr" | "rapidocr"; docling_page_batch_size: number };
  limits: {
    upload_max_bytes: number;
    timeout_per_page_s: number;
    timeout_min_s: number;
    timeout_per_mb_s: number;
    timeout_max_no_pages_s: number;
    startup_timeout_s: number;
    disk_space_factor: number;
  };
  server: { host: string; port: number; token: string };
}

export type SettingsPatch = { [S in keyof Settings]?: Partial<Settings[S]> };

export interface UploadCreated {
  upload_id: string;
  chunk_size: number;
  received: number;
}

export interface UploadPutResult {
  received: number;
  status: "receiving" | "complete";
}

export interface Chunk {
  id: string;
  source: string;
  heading_path: string[];
  page_start: number | null;
  page_end: number | null;
  text: string;
  oversized?: boolean;
}

export interface JobCreateBody {
  inputs: Array<{ upload_id: string } | { path: string }>;
  engine?: EngineName;
  lang?: Lang;
  force?: boolean;
  retry_low?: boolean;
  allow_online_audio?: boolean;
}

/** Every error body: {error: "<code>", ...extra, workspace}. */
export interface ApiErrorBody {
  error: string;
  [k: string]: unknown;
}

export interface EventPayloads {
  "job.updated": Job;
  "task.updated": Task;
  "segment.updated": Segment & { task_id: string };
  "task.log": { task_id: string; line: string; ts: number };
  "upload.updated": { id: string; received: number; status: string };
  "queue.updated": QueueInfo;
  "setup.log": { engine: string; line: string };
  "setup.done": { engine: string; ok: boolean; error?: string };
  "system.updated": Record<string, unknown>;
  resync: Record<string, never>;
}
export type EventKind = keyof EventPayloads;
export const EVENT_KINDS: EventKind[] = [
  "job.updated",
  "task.updated",
  "segment.updated",
  "task.log",
  "upload.updated",
  "queue.updated",
  "setup.log",
  "setup.done",
  "system.updated",
  "resync",
];

export type AidocEvent<K extends EventKind = EventKind> = K extends EventKind
  ? { kind: K; seq: number; payload: EventPayloads[K] }
  : never;
