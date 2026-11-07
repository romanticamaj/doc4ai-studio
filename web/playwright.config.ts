import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

// A throwaway doc4ai server: fake engines (index A9), its own data dir and doc4ai.toml so e2e never touches the
// project's data/ or out/. Written here (not in globalSetup) because the webServer starts before globalSetup.
const here = path.dirname(fileURLToPath(import.meta.url));
// 8781, not 8765: on the dev host an unrelated service holds 0.0.0.0:8765 and answers the pre-start probe
const port = Number(process.env.DOC4AI_E2E_PORT ?? 8781);
const owner = !process.env.DOC4AI_E2E_DIR;
const root = process.env.DOC4AI_E2E_DIR ?? fs.mkdtempSync(path.join(os.tmpdir(), "doc4ai-e2e-"));
process.env.DOC4AI_E2E_DIR = root; // workers re-import this file: keep one dir per run
if (owner) {
  // M6: the run's dir goes when the runner exits (after Playwright has stopped the server that held the DB open);
  // dirs left by runs that were killed are swept once they are an hour old
  process.on("exit", () => {
    try {
      fs.rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
    } catch {
      /* a later run sweeps it */
    }
  });
  for (const d of fs.readdirSync(os.tmpdir())) {
    if (!d.startsWith("doc4ai-e2e-")) continue;
    const full = path.join(os.tmpdir(), d);
    try {
      if (full !== root && Date.now() - fs.statSync(full).mtimeMs > 3600_000) fs.rmSync(full, { recursive: true, force: true });
    } catch {
      /* in use */
    }
  }
}
const data = path.join(root, "data");
const out = path.join(root, "out");
fs.mkdirSync(data, { recursive: true });
fs.mkdirSync(out, { recursive: true });
const scenario = path.join(root, "scenario.json");
fs.writeFileSync(scenario, JSON.stringify({ default: "ok", delay_s: 0.3, rules: [] }));
const vlmPort = Number(process.env.DOC4AI_E2E_VLM_PORT ?? 8782);
process.env.DOC4AI_E2E_VLM_PORT = String(vlmPort);
const vlmScenario = path.join(root, "vlm_scenario.json");
fs.writeFileSync(vlmScenario, JSON.stringify({ default: "ok", model: "fake-vlm", require_key: "sk-test", pages: { "1": "AIDOC VLM CHECK 繁體中文 2026" } }));
const config = path.join(root, "doc4ai.toml");
// the run's own Web UI build: web/dist may be served by a running server of this checkout and is never rewritten
const dist = path.join(root, "dist");
// the Doc4AI folder (out/ + source/) is the run's temp dir, set as root_dir (not DOC4AI_LIBRARY_ROOT, which would make
// the Settings field read-only): never the platform default C:\doc4ai / ~/doc4ai. sources.spec.ts moves it to
// <run dir>/lib through Settings; the doc4ai.toml it saves is this temp one.
fs.writeFileSync(config, `[general]\nroot_dir = ${JSON.stringify(root)}\n[server]\nport = ${port}\n`);

// The bundled Chromium download is blocked on some networks; the system Edge/Chrome works the same.
// PW_CHANNEL="" uses the bundled browser.
const channel = process.env.PW_CHANNEL ?? "msedge";
// `--project real` (pnpm e2e:real) targets an already running real server: no fake server is started for it
// (workers re-import this file without the CLI arguments: the decision is passed on in the environment)
const realOnly =
  process.env.DOC4AI_E2E_REAL_ONLY === "1" ||
  process.argv.some((v, i) => v === "--project=real" || (v === "real" && process.argv[i - 1] === "--project"));
if (realOnly) process.env.DOC4AI_E2E_REAL_ONLY = "1";

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
  // the real-sample project runs only with `--project real` (pnpm e2e:real): in a plain run it would only be skipped
  projects: [
    {
      name: "chromium",
      testIgnore: /sync-real\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], ...(channel ? { channel } : {}) },
    },
    {
      // spec 2026-10-01 §10.4: real engines; DOC4AI_E2E_REAL_BASE_URL / _DOC_ID / _PDF point at a converted sample
      name: "real",
      testMatch: /sync-real\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], ...(channel ? { channel } : {}), baseURL: process.env.DOC4AI_E2E_REAL_BASE_URL },
    },
  ].filter((p) => (p.name === "real") === realOnly),
  webServer: realOnly ? undefined : [{
    // --no-sync: a running server of this checkout holds the venv's aidoc.exe (a sync would fail to replace it)
    command: `pnpm exec vite build --outDir ${JSON.stringify(dist)} --emptyOutDir --logLevel warn && uv run --no-sync --project .. doc4ai serve --port ${port}`,
    cwd: here,
    url: `http://127.0.0.1:${port}/`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      DOC4AI_FAKE_ENGINES: "1",
      DOC4AI_FAKE_SCENARIO: scenario,
      DOC4AI_DATA: data,
      DOC4AI_ENVS: path.join(root, "envs"), // the VLM connection test writes envs/vlm/.ready: never the project's
      DOC4AI_CONFIG: config,
      DOC4AI_WEB_DIST: dist,
      DOC4AI_LIBRARY_ROOT: "", // an inherited override would hide root_dir above
      PYTHONUTF8: "1",
    },
  }, {
    // a scripted OpenAI-compatible VLM endpoint on loopback (vlm.spec.ts: Settings → connection test)
    command: `uv run --no-sync python -m tests.fakes.vlm_server --port ${vlmPort} --scenario ${JSON.stringify(vlmScenario)}`,
    cwd: path.resolve(here, ".."),
    url: `http://127.0.0.1:${vlmPort}/v1/models`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: { PYTHONUTF8: "1" },
  }],
});
