import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "phase14-rendering.spec.ts",
  timeout: 120_000,
  workers: 1,
  outputDir: "/tmp/mito-phase14-playwright-results",
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:4174",
    headless: true,
    launchOptions: {
      executablePath: "/snap/bin/chromium",
      args: ["--enable-precise-memory-info", "--no-sandbox"],
    },
  },
  webServer: {
    command: "VITE_FEATURE_CHUNK_RENDERER=true exec ./node_modules/.bin/vite --host 127.0.0.1 --port 4174 --strictPort",
    url: "http://127.0.0.1:4174/e2e/fixtures/phase14-harness.html",
    reuseExistingServer: false,
  },
});
