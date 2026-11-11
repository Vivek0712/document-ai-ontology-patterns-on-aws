/**
 * Playwright e2e (mock mode, headless, port 5188): route smoke, a keyboard-only run of the story tour, and an axe
 * scan of every page in both themes. Uses the installed Google Chrome (`channel: "chrome"`), so no browser
 * download is needed. `npm run e2e` starts `vite --mode mock` itself; locally it reuses one already on 5188 (in CI it
 * never does), and every spec checks the MOCK badge, so a non-mock server on the port fails instead of passing.
 */
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 2,
  reporter: [["list"]],
  outputDir: "e2e/.results",
  use: {
    baseURL: "http://localhost:5188",
    channel: "chrome",
    headless: true,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev:mock",
    url: "http://localhost:5188",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
