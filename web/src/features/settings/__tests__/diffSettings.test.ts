import { expect, test } from "vitest";
import { diffSettings } from "../diffSettings";

const a = {
  general: { lang: "cht", output_dir: "out", work_retention_days: 7, enable_audio: false },
  engines: { mineru_tier: "basic", docling_ocr: "easyocr", docling_page_batch_size: 16 },
  limits: { upload_max_bytes: 1 },
  server: { host: "127.0.0.1", port: 8765, token: "" },
  mcp: { enabled: true, allowed_hosts: [], local_path_roots: [], default_token_ttl_days: 90, max_token_ttl_days: 365, allow_no_expiry: false,
    rate_limit_per_min: 60, max_concurrent_jobs_per_token: 3, max_upload_mb: 20, response_token_budget: 8000, call_log_retention_days: 30,
    call_log_max_rows: 200000 },
} as any;

test("only changed keys are sent, grouped by section", () => {
  const b = { ...a, general: { ...a.general, lang: "en" }, engines: { ...a.engines, mineru_tier: "standard" } };
  expect(diffSettings(a, b)).toEqual({ general: { lang: "en" }, engines: { mineru_tier: "standard" } });
  expect(diffSettings(a, a)).toEqual({});
});

test("server settings are never sent (read-only; token is masked)", () => {
  const b = { ...a, server: { host: "0.0.0.0", port: 1, token: "***" }, limits: { upload_max_bytes: 2 } };
  expect(diffSettings(a, b)).toEqual({ limits: { upload_max_bytes: 2 } });
});

test("list values are compared by content", () => {
  const x = { ...a, mcp: { ...a.mcp, allowed_hosts: ["x"], local_path_roots: [] } };
  const y = { ...x, mcp: { ...x.mcp, allowed_hosts: ["x"], local_path_roots: ["C:/docs"] } };
  expect(diffSettings(x, y)).toEqual({ mcp: { local_path_roots: ["C:/docs"] } });
});
