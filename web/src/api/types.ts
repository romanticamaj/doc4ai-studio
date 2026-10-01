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
export type DocStatus = "ok" | "warn" | "low" | "orphaned";
export type DocFlag = "page_map_incomplete" | "page_quality" | "unassessed";
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
  origin: "cli" | "web" | "mcp";
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

export interface PageAlignment {
  sampled: number;
  decidable: number;
  aligned: number;
  ratio: number | null;
  misplaced: number[];
  excluded_pages: number;
}

export interface PageMap {
  expected: number;
  found: number;
  coverage: number;
  missing: number[];
  missing_truncated?: boolean;
  method: string | null;
  alignment?: PageAlignment | null;
}

/** A flagged page (spec 2026-10-01 §5.1): reasons page_map_missing | broken_text_layer | garbage. */
export interface PageFlag {
  page: number;
  reasons: string[];
  metrics?: Record<string, number>;
  repaired_by?: string;
}

export interface Quality {
  score: number;
  level: "ok" | "warn" | "low";
  reasons: string[];
  metrics?: Record<string, unknown>;
  page_check?: number;
  page_map?: PageMap | null;
  pages?: PageFlag[];
  pages_flagged?: number;
  pages_unrepaired?: number;
}

export interface PageSummary {
  expected: number;
  found: number;
  coverage: number;
  flagged: number;
  unrepaired: number;
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
  flags?: { force?: boolean; auto_engine?: boolean; reconvert?: boolean };
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
  flags: DocFlag[];
  page_summary: PageSummary | null;
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
  mcp: {
    enabled: boolean;
    allowed_hosts: string[];
    local_path_roots: string[];
    default_token_ttl_days: number;
    max_token_ttl_days: number;
    allow_no_expiry: boolean;
    rate_limit_per_min: number;
    max_concurrent_jobs_per_token: number;
    max_upload_mb: number;
    response_token_budget: number;
    call_log_retention_days: number;
    call_log_max_rows: number;
  };
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
  "mcp.call": McpCallEvent;
  "mcp.client": McpClient & { state: "new" | "active" | "idle" };
  "mcp.token": { id: string; name: string; prefix: string; action: "created" | "updated" | "revoked" | "rotated" };
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
  "mcp.call",
  "mcp.client",
  "mcp.token",
  "resync",
];

export type AidocEvent<K extends EventKind = EventKind> = K extends EventKind
  ? { kind: K; seq: number; payload: EventPayloads[K] }
  : never;

// ---- MCP admin (plan index §4 /api/mcp/*)
export type McpScope = "doc4ai:read" | "doc4ai:convert" | "doc4ai:convert:local" | "doc4ai:manage";
export type McpCallStatus = "ok" | "tool_error" | "protocol_error" | "auth_error" | "forbidden_scope" | "rate_limited";

export interface McpStatus {
  enabled: boolean; endpoint_urls: string[]; bind: { host: string; port: number }; allowed_hosts: string[];
  protocol_versions: string[]; sdk_version: string; active_clients: number; calls_24h: number; errors_24h: number;
  tokens_expiring_soon: number; local_path_roots: string[]; plaintext_http: boolean; tokenizer: string;
  config_warnings?: string[];
}
export interface McpToken {
  id: string; name: string; prefix: string; scopes: McpScope[]; note: string | null; created_at: number; expires_at: number | null;
  revoked_at: number | null; revoked_reason: string | null; rotated_from: string | null; rate_limit_per_min: number | null;
  last_used_at: number | null; last_used_ip: string | null; last_client: string | null; status: "active" | "expired" | "revoked";
  calls_24h: number; errors_24h: number;
}
export interface McpSnippet { client: "claude-code" | "cursor" | "vscode" | "claude-desktop"; title: string; language: "bash" | "json"; text: string }
export interface McpTokenCreated { token: string; record: McpToken; snippets: McpSnippet[]; revoked?: McpToken }
export interface McpClient {
  id: string; token_id: string | null; token_name: string | null; client_name: string; client_version: string | null;
  protocol_version: string | null; user_agent: string | null; first_seen: number; last_seen: number; last_ip: string | null;
  request_count: number; active: boolean;
}
export interface McpCall {
  id: number; ts: number; token_id: string | null; token_prefix_seen: string | null; client_id: string | null; method: string | null;
  tool_name: string | null; resource_uri: string | null; args_summary: string | null; status: McpCallStatus; error_code: string | null;
  http_status: number | null; duration_ms: number | null; response_bytes: number | null; response_tokens_est: number | null;
  ip: string | null; protocol_version: string | null; job_id: string | null; token_name: string | null; client_name: string | null;
}
export interface McpCallsPage { calls: McpCall[]; next_cursor: string | null }
export interface McpStats {
  window: "24h" | "7d"; since: number;
  tools: { tool: string; calls: number; errors: number; error_rate: number; p50_ms: number | null; p95_ms: number | null; tokens_median: number | null }[];
  tokens: { token_id: string; name: string | null; calls: number; errors: number }[];
  series: { ts: number; calls: number; errors: number }[];
}
export type McpCallEvent = Pick<McpCall, "id" | "ts" | "token_id" | "token_name" | "client_id" | "client_name" | "method" | "tool_name" |
  "status" | "error_code" | "http_status" | "duration_ms" | "response_tokens_est" | "job_id" | "token_prefix_seen" | "ip" |
  "protocol_version" | "response_bytes" | "resource_uri">;
