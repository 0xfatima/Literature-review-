import { defineConfig } from "@playwright/test";

// Expects the full stack (proxy+web+api+db+redis+worker) to be running; see scripts/test-e2e.sh.
// Optional, for environments where Playwright's own browser download is blocked: point
// PLAYWRIGHT_CHROMIUM_EXECUTABLE at a Chromium binary (and PLAYWRIGHT_CHROMIUM_ARGS at a JSON array of flags).
// CI does not set these and uses the browser installed by `npx playwright install`.
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined;
const args = process.env.PLAYWRIGHT_CHROMIUM_ARGS
  ? (JSON.parse(process.env.PLAYWRIGHT_CHROMIUM_ARGS) as string[])
  : [];

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    launchOptions: executablePath ? { executablePath, args } : undefined,
  },
});
