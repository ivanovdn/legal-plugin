import { defineConfig, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
import { getHttpsServerOptions } from "office-addin-dev-certs";

// Word's task-pane WKWebView caches localhost modules aggressively and its HMR
// websocket often can't connect over the self-signed cert — so edits silently
// don't reach the pane. Force every dev-server response to be uncacheable so a
// pane reload always fetches fresh code.
const noStore: PluginOption = {
  name: "no-store-cache-control",
  configureServer(server) {
    server.middlewares.use((_req, res, next) => {
      res.setHeader("Cache-Control", "no-store, no-cache, must-revalidate");
      next();
    });
  },
};

export default defineConfig(async () => {
  const https = await getHttpsServerOptions();
  return {
    plugins: [react(), noStore],
    root: "src",
    publicDir: "../assets",
    server: {
      // Bind IPv4 loopback explicitly. With host:"localhost", Vite 5 binds
      // IPv6 [::1] only, but Word for Mac's WKWebView reaches localhost over
      // IPv4 127.0.0.1 — so the pane silently fails to load fresh content (or
      // errors with "can't load add-in"). 127.0.0.1 is reachable whether the
      // webview tries v4 directly or falls back from a refused v6.
      host: "127.0.0.1",
      port: 3001,
      https,
      proxy: {
        // Only forward /api/... (trailing slash) so /api.ts (the source module)
        // continues to be served by Vite, not proxied to the backend.
        "^/api/": {
          target: "http://localhost:8000",
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: "../dist",
      emptyOutDir: true,
    },
  };
});
