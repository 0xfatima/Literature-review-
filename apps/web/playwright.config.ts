import { defineConfig } from "@playwright/test";
// Expects the full stack (web+api+db+redis+worker) to be running; see scripts/test-e2e.sh.
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000", trace: "retain-on-failure" },
});
