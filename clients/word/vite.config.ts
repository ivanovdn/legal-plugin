import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { getHttpsServerOptions } from "office-addin-dev-certs";

export default defineConfig(async () => {
  const https = await getHttpsServerOptions();
  return {
    plugins: [react()],
    root: "src",
    publicDir: "../assets",
    server: {
      host: "localhost",
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
