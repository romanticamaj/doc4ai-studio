import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, type Plugin } from "vite";

const here = path.dirname(fileURLToPath(import.meta.url));
const pdfjsDir = path.join(here, "node_modules", "pdfjs-dist");
const PDFJS_ASSETS = ["standard_fonts", "cmaps"];

/** pdf.js needs its standard fonts and CJK character maps at runtime: serve them at /pdfjs/* (dev) and copy them
 *  into dist/pdfjs/ (build), so the app works offline and pdf.js stops warning about standardFontDataUrl. */
function pdfjsAssets(): Plugin {
  let outDir = "dist";
  return {
    name: "aidoc-pdfjs-assets",
    configResolved(c) {
      outDir = path.resolve(c.root, c.build.outDir);
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const m = /^\/pdfjs\/(standard_fonts|cmaps)\/([A-Za-z0-9._-]+)$/.exec((req.url ?? "").split("?")[0]);
        if (!m) return next();
        const file = path.join(pdfjsDir, m[1], m[2]);
        if (!fs.existsSync(file)) return next();
        res.setHeader("Content-Type", "application/octet-stream");
        fs.createReadStream(file).pipe(res);
      });
    },
    writeBundle() {
      for (const d of PDFJS_ASSETS) fs.cpSync(path.join(pdfjsDir, d), path.join(outDir, "pdfjs", d), { recursive: true });
    },
  };
}

// The dev proxy must target a loopback name: without a token the API only answers loopback Host names
// (DNS-rebinding guard, index §8 "as implemented in P3"). changeOrigin rewrites Host to 127.0.0.1:8765.
export default defineConfig({
  plugins: [react(), tailwindcss(), pdfjsAssets()],
  resolve: { alias: { "@": path.join(here, "src") } },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
        // same-origin rule for non-GET: rewrite the browser's Origin to the target's
        configure: (proxy) => {
          proxy.on("proxyReq", (req) => {
            if (req.getHeader("origin")) req.setHeader("origin", "http://127.0.0.1:8765");
          });
        },
      },
    },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 1500 },
});
