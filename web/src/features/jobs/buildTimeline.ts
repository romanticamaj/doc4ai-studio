import type { Attempt, Job, Task } from "@/api/types";

export type StepState = "done" | "active" | "failed" | "warn" | "pending" | "skipped";
export type TimelineStep = { key: string; title: string; detail?: string; state: StepState };

const ACTIVE = new Set(["probing", "converting", "checking"]);

const REASON_LABEL: Record<string, string> = {
  chars_per_page: "每頁字數過少",
  garbage_ratio: "亂碼比例過高",
  missing_table: "原檔有表格但輸出沒有",
};

export function reasonText(reasons: string[] | undefined): string {
  return (reasons ?? []).map((r) => `${REASON_LABEL[r] ?? r} (${r})`).join("、");
}

function attemptDetail(a: Attempt): string | undefined {
  if (a.error_kind) return `${a.error_kind}: ${a.error_msg ?? ""}`.trim();
  const parts: string[] = [];
  if (a.score != null) parts.push(`品質 ${a.score.toFixed(2)}`);
  if (a.reasons?.length) parts.push(reasonText(a.reasons));
  return parts.length ? parts.join(" · ") : undefined;
}

const FINAL: Record<string, { title: string; state: StepState; detail?: string }> = {
  done: { title: "done", state: "done", detail: "輸出已寫入" },
  low: { title: "low", state: "warn", detail: "已輸出，但品質偏低" },
  failed: { title: "failed", state: "failed" },
  skipped: { title: "skipped", state: "done", detail: "快取命中：沿用先前的結果" },
  cancelled: { title: "cancelled", state: "skipped", detail: "已取消；重試會從未完成的段落繼續" },
};

/**
 * Task timeline (spec §6 page 2): probe → each engine attempt (with fallback reasons) → quality → final status.
 * The pipeline records an attempt when it ends, so while converting the engine in use is shown as an extra step
 * unless the last recorded attempt is still open (no score and no error).
 */
export function buildTimeline(task: Task): TimelineStep[] {
  const status = task.status;
  const active = ACTIVE.has(status);
  const tried = task.tried ?? [];
  const steps: TimelineStep[] = [];

  const pages = task.pages ?? (task.progress?.pages_total && task.segments?.some((s) => s.page_start != null) ? task.progress.pages_total : null);
  steps.push({
    key: "probe",
    title: "偵測",
    detail: status === "queued" ? "排隊中" : pages ? `${pages} 頁` : status === "probing" ? "分析檔案類型與內容" : undefined,
    state: status === "queued" && tried.length === 0 ? "pending" : status === "probing" ? "active" : "done",
  });

  const last = tried[tried.length - 1];
  const lastOpen = !!last && active && status !== "probing" && last.score == null && !last.error_kind;
  tried.forEach((a, i) => {
    const isLast = i === tried.length - 1;
    let state: StepState;
    if (a.error_kind) state = "failed";
    else if (!isLast) state = "failed"; // a later attempt exists: this one fell back
    else if (lastOpen) state = "active";
    else if (active) state = "failed"; // a new attempt is running after this one
    else if (status === "done" || status === "low" || status === "skipped") state = status === "low" ? "warn" : "done";
    else if (status === "failed") state = "failed";
    else if (status === "cancelled") state = "skipped";
    else state = "pending";
    steps.push({ key: `attempt-${i + 1}`, title: `使用 ${a.engine}`, detail: attemptDetail(a), state });
  });

  if (active && !lastOpen && status !== "probing" && task.engine) {
    steps.push({
      key: `attempt-${tried.length + 1}`,
      title: `使用 ${task.engine}`,
      detail: status === "checking" ? "檢查品質中" : "轉換中",
      state: "active",
    });
  }

  if (task.quality) {
    const q = task.quality;
    steps.push({
      key: "quality",
      title: "品質分數",
      detail: [`${q.score.toFixed(2)} · ${q.level}`, reasonText(q.reasons)].filter(Boolean).join(" · "),
      state: q.level === "ok" ? "done" : "warn",
    });
  }

  const fin = FINAL[status];
  if (fin) {
    const dup = status === "skipped" && (task.error_msg ?? "").startsWith("duplicate_of:");
    const detail = dup
      ? `與同一工作中的 ${(task.error_msg ?? "").slice("duplicate_of:".length).trim()} 內容相同，只轉換一次`
      : status === "failed"
        ? [task.error_kind, task.error_msg].filter(Boolean).join(": ") || undefined
        : fin.detail;
    steps.push({ key: "final", title: fin.title, detail, state: fin.state });
  } else {
    steps.push({ key: "final", title: "完成", state: "pending" });
  }
  return steps;
}

/** True when progress counts pages (segments have page ranges, or none are known yet). */
function hasPages(task: Task): boolean {
  const segs = task.segments ?? [];
  return segs.length === 0 || segs.some((s) => s.page_start != null);
}

export function progressLabel(task: Task): string {
  const p = task.progress;
  if (!p?.pages_total || !hasPages(task)) return "—";
  return `第 ${p.pages_done}/${p.pages_total} 頁`;
}

/** 0..100, never NaN. */
export function percent(task: Task): number {
  const p = task.progress;
  if (!p?.pages_total) return 0;
  const v = (p.pages_done / p.pages_total) * 100;
  return Number.isFinite(v) ? Math.round(v) : 0;
}

export function jobPercent(job: Pick<Job, "progress">): number {
  const p = job?.progress;
  if (!p?.total) return 0;
  const v = (p.done / p.total) * 100;
  return Number.isFinite(v) ? Math.round(v) : 0;
}

export function isTerminalJob(status: string): boolean {
  return status === "done" || status === "cancelled";
}
