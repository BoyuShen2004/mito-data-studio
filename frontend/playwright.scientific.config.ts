import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "scientific-workbench.spec.ts",
  timeout: 45_000,
  workers: 1,
  outputDir: "/tmp/mito-scientific-playwright-results",
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:5180",
    headless: true,
    launchOptions: { executablePath: "/snap/bin/chromium", args: ["--no-sandbox"] },
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npm run dev -- --port 5180 --strictPort",
    url: "http://127.0.0.1:5180",
    reuseExistingServer: false,
  },
});
