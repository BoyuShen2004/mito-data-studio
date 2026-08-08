import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Where the Django API lives. dev.sh sets VITE_BACKEND_URL to match the
// backend host/port; defaults to the local Django dev server.
const backend = process.env.VITE_BACKEND_URL || "http://127.0.0.1:8000";

// Dev-server host/port, overridable for remote/HPC use (e.g. VITE_HOST=0.0.0.0).
const host = process.env.VITE_HOST || "127.0.0.1";
const port = Number(process.env.VITE_PORT || "5173");

export default defineConfig({
  plugins: [react()],
  // Vitest. jsdom only for component/lifecycle tests; the pure modules
  // (pendingSliceBuffer, sliceHistory, revisionedFetch) do not need a DOM and
  // run in either environment. `setupFiles` installs the canvas stub jsdom
  // lacks — see src/test/setup.ts.
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    // Vitest stubs CSS imports to an empty string by default, which makes the
    // stylesheet untestable. `topbarLayout.test.ts` asserts the reserved-width
    // rules that keep topbar controls from overlapping, and it needs the real
    // text. Only that test imports CSS, so nothing else is affected.
    css: true,
  },
  server: {
    host,
    port,
    // Vite checks the incoming request's Host header against an allowlist
    // (DNS-rebinding protection) and otherwise silently rejects it — the
    // request never even reaches this app. That allowlist only knows
    // localhost/127.0.0.1/the configured `host` by default, but remote-dev
    // proxies (VS Code/Cursor Remote-SSH port forwarding, SSH tunnels
    // through a jump host, opening via a machine's real network IP) can
    // present a completely different Host header, so the request gets
    // blocked before Vite even serves the page — this can look identical to
    // "nothing is listening" from the browser. Disabled here since this is
    // a local/HPC dev server, never a public deployment.
    allowedHosts: true,
    // The dev server proxies API + media requests to the Django backend so the
    // SPA can use same-origin relative URLs.
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      "/media": { target: backend, changeOrigin: true },
    },
  },
});
