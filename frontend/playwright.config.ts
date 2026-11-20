import { defineConfig, devices } from "@playwright/test";

/**
 * Browser end-to-end tests exercise a real Next.js frontend and a real FastAPI
 * backend over HTTP. The backend is started by the global setup with temporary
 * SQLite databases and deterministic fake model providers; it is not mocked.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  globalSetup: "./e2e/support/global-setup.ts",
  globalTeardown: "./e2e/support/global-teardown.ts",
  expect: { timeout: 10_000 },
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      testIgnore: /config-required\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: "http://localhost:3000" },
    },
    {
      // A second unconfigured frontend instance proves the OIDC-missing
      // screen without development authentication. It starts on port 3001.
      name: "oidc-unconfigured",
      testMatch: /config-required\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: "http://localhost:3001" },
    },
  ],
  webServer: [
    {
      command: "npm run dev",
      url: "http://localhost:3000",
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        NEXT_PUBLIC_OPSPILOT_API_URL: "http://127.0.0.1:8000",
        NEXT_PUBLIC_OPSPILOT_DEV_AUTH: "true",
      },
    },
    {
      // A production build with no OIDC configuration and no development-auth
      // flag. This also proves that dev auth is not compiled into a normal
      // production build.
      command: "npm run build && npm run start -- --port 3001",
      url: "http://localhost:3001",
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        NEXT_DIST_DIR: ".next-unconfigured",
        NEXT_PUBLIC_OPSPILOT_API_URL: "http://127.0.0.1:8000",
        NEXT_PUBLIC_OPSPILOT_DEV_AUTH: "",
      },
    },
  ],
});
