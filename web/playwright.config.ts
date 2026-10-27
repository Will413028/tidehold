import { defineConfig, devices } from "@playwright/test";

const BACKEND = "http://127.0.0.1:8100";
const WEB = "http://localhost:3100";

/** The demo path in a real browser at desktop width, against the e2e backend (step 9). */
export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  // Under make check each API call took 1-2 s (measured 2026-10-20 from a failing trace: a
  // backend connection per request through Docker's port), so a three-call step can pass 5 s.
  expect: { timeout: 15_000 },
  // `make e2e` copies the junit report next to the other suites' (quality-lock.json counts it)
  reporter: [["list"], ["junit", { outputFile: "test-results/junit.xml" }]],
  use: { baseURL: WEB, trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 900 } } },
  ],
  webServer: [
    {
      // the real app with PayPal, the model and Open-Meteo faked (backend/tests/e2e/server.py)
      command: "uv run --frozen python -m tests.e2e.server",
      cwd: "../backend",
      url: `${BACKEND}/api/health`,
      timeout: 120_000,
      reuseExistingServer: false,
      // SIGTERM lets the server stop its PostgreSQL container; the default kills it
      gracefulShutdown: { signal: "SIGTERM", timeout: 15_000 },
    },
    {
      command: "pnpm build && pnpm start --port 3100",
      env: { BACKEND_URL: BACKEND },
      url: WEB,
      timeout: 240_000,
      reuseExistingServer: false,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
    },
  ],
});
