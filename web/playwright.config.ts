import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

// A throwaway aidoc server: fake engines (index A9), its own data dir and aidoc.toml so e2e never touches the
// project's data/ or out/. Written here (not in globalSetup) because the webServer starts before globalSetup.
const here = path.dirname(fileURLToPath(import.meta.url));
// 8781, not 8765: on the dev host an unrelated service holds 0.0.0.0:8765 and answers the pre-start probe
const port = Number(process.env.AIDOC_E2E_PORT ?? 8781);
const root = process.env.AIDOC_E2E_DIR ?? fs.mkdtempSync(path.join(os.tmpdir(), "aidoc-e2e-"));
process.env.AIDOC_E2E_DIR = root; // workers re-import this file: keep one dir per run
const data = path.join(root, "data");
const out = path.join(root, "out");
fs.mkdirSync(data, { recursive: true });
fs.mkdirSync(out, { recursive: true });
const scenario = path.join(root, "scenario.json");
fs.writeFileSync(scenario, JSON.stringify({ default: "ok", delay_s: 0.3, rules: [] }));
const config = path.join(root, "aidoc.toml");
fs.writeFileSync(config, `[general]\noutput_dir = ${JSON.stringify(out.replace(/\\/g, "/"))}\n[server]\nport = ${port}\n`);

// The bundled Chromium download is blocked on some networks; the system Edge/Chrome works the same.
// PW_CHANNEL="" uses the bundled browser.
const channel = process.env.PW_CHANNEL ?? "msedge";

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 60_000,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
    ...(channel ? { channel } : {}),
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], ...(channel ? { channel } : {}) } }],
  webServer: {
    command: `uv run aidoc serve --port ${port}`,
    cwd: path.resolve(here, ".."),
    url: `http://127.0.0.1:${port}/`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      AIDOC_FAKE_ENGINES: "1",
      AIDOC_FAKE_SCENARIO: scenario,
      AIDOC_DATA: data,
      AIDOC_CONFIG: config,
      PYTHONUTF8: "1",
    },
  },
});
