import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// The dev proxy must target a loopback name: without a token the API only answers loopback Host names
// (DNS-rebinding guard, index §8 "as implemented in P3"). changeOrigin rewrites Host to 127.0.0.1:8765.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
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
