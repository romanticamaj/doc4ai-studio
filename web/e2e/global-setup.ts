// The e2e server builds its own copy of the Web UI (playwright.config.ts webServer: vite build --outDir <run dir>)
// and serves it with DOC4AI_WEB_DIST, so nothing here touches web/dist: a server already running from this checkout
// keeps serving the build it started with.
export default function globalSetup() {}
