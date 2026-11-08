import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const { VITE_API_PROXY_TARGET: apiProxyTarget = "http://localhost:8000" } = env;

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": { target: apiProxyTarget, changeOrigin: true },
      },
    },
    // `vite preview` serves the production build; the end-to-end tests use it.
    preview: {
      port: 4173,
      strictPort: true,
      proxy: {
        "/api": { target: apiProxyTarget, changeOrigin: true },
      },
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      // Playwright's end-to-end specs run separately.
      include: ["src/**/*.test.{ts,tsx}"],
      css: false,
    },
  };
});
