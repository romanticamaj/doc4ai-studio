import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// aidoc serve serves web/dist: build it when it is missing (a stale build is the caller's responsibility).
export default function globalSetup() {
  const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  if (!fs.existsSync(path.join(web, "dist", "index.html"))) execSync("pnpm build", { cwd: web, stdio: "inherit" });
}
