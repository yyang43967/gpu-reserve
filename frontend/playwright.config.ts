import { defineConfig, devices } from "@playwright/test";

import { API_URL, APP_URL } from "./e2e/env";

/**
 * End-to-end tests against the stack in docker-compose.e2e.yml, which must be running.
 * Playwright builds the frontend and serves it with `vite preview`, proxying /api to
 * that stack. See e2e/README.md.
 */
export default defineConfig({
  testDir: "./e2e",
  // The approval email arrives on the worker's next minute tick.
  timeout: 150_000,
  expect: { timeout: 10_000 },
  // The specs share one database; run them one after another.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: APP_URL,
    trace: "retain-on-failure",
    locale: "en-US",
    // A zone no lab uses: every time on screen must come from the lab's zone.
    timezoneId: "Asia/Tokyo",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "pnpm build && pnpm exec vite preview",
    url: APP_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    env: { VITE_API_PROXY_TARGET: API_URL },
  },
});
