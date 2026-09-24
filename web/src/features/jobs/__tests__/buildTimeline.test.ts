import { expect, test } from "vitest";
import { buildTimeline, jobPercent, percent, progressLabel } from "../buildTimeline";

const base = {
  id: "t",
  job_id: "j",
  status: "done",
  engine: "mineru",
  attempt: 2,
  segments: [],
  quality: { score: 0.93, level: "ok", reasons: [] },
  tried: [
    { engine: "docling", attempt: 1, score: 0.3, reasons: ["missing_table"] },
    { engine: "mineru", attempt: 2, score: 0.93, reasons: [] },
  ],
  progress: { pages_done: 120, pages_total: 300 },
} as any;

test("timeline shows fallback with reason", () => {
  const steps = buildTimeline(base);
  expect(steps.map((s) => s.key)).toEqual(["probe", "attempt-1", "attempt-2", "quality", "final"]);
  expect(steps[1]).toMatchObject({ state: "failed", title: "使用 docling" });
  expect(steps[1].detail).toContain("missing_table");
  expect(steps[2].state).toBe("done");
  expect(steps[3].detail).toContain("0.93");
  expect(steps[4].state).toBe("done");
});

test("active step while converting and safe progress", () => {
  const t = { ...base, status: "converting", quality: null, tried: [{ engine: "docling", attempt: 1, score: null, reasons: [] }] };
  expect(buildTimeline(t).find((s) => s.key === "attempt-1")!.state).toBe("active");
  expect(progressLabel(base)).toBe("第 120/300 頁");
  expect(percent(base)).toBe(40);
  expect(percent({ ...base, progress: { pages_done: 0, pages_total: null } })).toBe(0);
  expect(progressLabel({ ...base, progress: { pages_done: 0, pages_total: null } })).toBe("—");
});

test("the engine in use is shown before its attempt is recorded", () => {
  const t = { ...base, status: "converting", engine: "docling", quality: null, tried: [] };
  const steps = buildTimeline(t);
  expect(steps.map((s) => s.key)).toEqual(["probe", "attempt-1", "final"]);
  expect(steps[1]).toMatchObject({ title: "使用 docling", state: "active" });
  expect(steps[2].state).toBe("pending");
});

test("an engine error is a failed step with its message", () => {
  const t = {
    ...base,
    status: "failed",
    quality: null,
    error_kind: "engine",
    error_msg: "runner crashed",
    tried: [{ engine: "docling", attempt: 1, score: null, reasons: [], error_kind: "engine", error_msg: "runner crashed" }],
  };
  const steps = buildTimeline(t);
  expect(steps[1]).toMatchObject({ state: "failed" });
  expect(steps[1].detail).toContain("runner crashed");
  expect(steps.at(-1)).toMatchObject({ key: "final", state: "failed" });
});

test("queued task: probe pending; low quality is a warning, not a failure", () => {
  expect(buildTimeline({ ...base, status: "queued", tried: [], quality: null })[0].state).toBe("pending");
  const low = buildTimeline({ ...base, status: "low", quality: { score: 0.2, level: "low", reasons: ["chars_per_page"] }, tried: [{ engine: "markitdown", attempt: 1, score: 0.2, reasons: ["chars_per_page"] }] });
  expect(low.find((s) => s.key === "quality")).toMatchObject({ state: "warn" });
  expect(low.find((s) => s.key === "quality")!.detail).toContain("chars_per_page");
});

test("docx-like task (one segment, no page range) has no page label and no NaN", () => {
  const t = { ...base, pages: null, segments: [{ id: "s", idx: 0, page_start: null, page_end: null, status: "done", attempt: 1 }], progress: { pages_done: 1, pages_total: 1 } };
  expect(progressLabel(t)).toBe("—");
  expect(Number.isNaN(percent(t))).toBe(false);
});

test("job with 0 tasks renders 0 %, never NaN", () => {
  expect(jobPercent({ progress: { done: 0, total: 0 } } as any)).toBe(0);
  expect(jobPercent({ progress: { done: 1, total: 4 } } as any)).toBe(25);
  expect(jobPercent({} as any)).toBe(0);
});
