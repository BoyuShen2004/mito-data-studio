import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "staging-release.spec.ts",
  timeout: 180_000,
  workers: 1,
  fullyParallel: false,
  outputDir:
    process.env.MITO_STAGING_PLAYWRIGHT_OUTPUT_DIR ??
    "/tmp/mito-staging-playwright-results",
  reporter: "line",
  use: {
    baseURL: process.env.MITO_STAGING_BASE_URL ?? "http://127.0.0.1:18189",
    // Private staging uses a self-signed TLS reverse proxy so native image
    // loads and fetch/XHR exercise the same ingress contract.
    ignoreHTTPSErrors: true,
    headless: true,
    launchOptions: {
      executablePath: "/snap/bin/chromium",
      args: ["--enable-precise-memory-info", "--no-sandbox"],
    },
    // Playwright's trace resource snapshotter re-fetches authenticated binary
    // slice responses outside the page network stack without copying the
    // Token header. That creates one artificial 401 and a second upstream read
    // per slice, invalidating production-load soak measurements. Keep traces
    // for ordinary release tests, but disable them for the explicit long soak.
    trace: process.env.MITO_STAGING_SOAK_SECONDS ? "off" : "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
