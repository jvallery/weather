// SPDX-License-Identifier: MIT
import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e", testMatch: "**/*.spec.ts", fullyParallel: false, workers: 1,
  timeout: 30_000, retries: 0, outputDir: "../artifacts/playwright",
  reporter: [["list"], ["json", { outputFile: "../artifacts/playwright-results.json" }]],
  use: { baseURL: "http://localhost:4173", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }, { name: "webkit", use: { ...devices["Desktop Safari"] } }, { name: "firefox", use: { ...devices["Desktop Firefox"] } }],
  webServer: [
    { command: "node e2e/fixture-api.mjs", url: "http://localhost:4174/healthz", reuseExistingServer: false, timeout: 30_000, env: { WEATHER_CI_IN_CLUSTER: "1" } },
    { command: "node server.mjs", url: "http://localhost:4175/healthz", reuseExistingServer: false, timeout: 30_000, env: { WEATHER_CI_IN_CLUSTER: "1", NODE_ENV: "production", API_ORIGIN: "http://localhost:4174", PORT: "4175", METRICS_PORT: "9175" } },
    { command: "node e2e/fixture-ingress.mjs", url: "http://localhost:4173/healthz", reuseExistingServer: false, timeout: 30_000, env: { WEATHER_CI_IN_CLUSTER: "1" } },
  ],
});
