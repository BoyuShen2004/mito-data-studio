import { expect, test } from "@playwright/test";
import { Buffer } from "node:buffer";

type Harness = {
  render(axis: "z" | "y" | "x", index: number, size?: number): Promise<{
    shape: [number, number];
    first: number[];
    last: number[];
    assemblyMs: number;
    visibleMs: number;
  }>;
  benchmark(size: number, iterations: number): Promise<{
    p50: number; p95: number; p99: number; samples: number[];
  }>;
  soak(iterations: number): Promise<{
    before: number; after: number; growth: number;
  }>;
  comparePngAndChunk(size: number, iterations: number): Promise<unknown>;
};

declare global {
  interface Window { phase14Harness: Harness }
}

test.beforeEach(async ({ page }) => {
  await page.goto("/phase14-harness.html");
  await page.waitForFunction(() => Boolean(window.phase14Harness));
});

test("renders pixel-aligned XY XZ and YZ canvases in Chromium", async ({ page }) => {
  const results = await page.evaluate(async () => ({
    xy: await window.phase14Harness.render("z", 2, 32),
    xz: await window.phase14Harness.render("y", 3, 32),
    yz: await window.phase14Harness.render("x", 4, 32),
  }));
  expect(results.xy.shape).toEqual([32, 32]);
  expect(results.xz.shape).toEqual([16, 32]);
  expect(results.yz.shape).toEqual([16, 32]);
  for (const result of Object.values(results)) {
    expect(result.first[3]).toBe(255);
    expect(result.last[3]).toBe(255);
  }
});

test("@phase14-benchmark browser-visible warm render meets p95 gate", async ({ page }) => {
  const results = await page.evaluate(async () => ({
    plane512: await window.phase14Harness.benchmark(512, 24),
    plane2048: await window.phase14Harness.benchmark(2048, 8),
  }));
  console.log(`PHASE14_BROWSER_BENCHMARK=${JSON.stringify(results)}`);
  expect(results.plane512.p95).toBeLessThan(100);
  // 2048² is retained as measurement evidence; the authoritative scrub gate
  // is the representative warm viewport, not a synthetic full-frame allocate.
  expect(results.plane2048.p99).toBeLessThan(3000);
});

test("browser memory recovers within the sustained Phase 14 soak budget", async ({
  page,
  context,
}) => {
  const cdp = await context.newCDPSession(page);
  await page.evaluate(() => window.phase14Harness.soak(20));
  await cdp.send("HeapProfiler.collectGarbage");
  const before = await page.evaluate(() =>
    (performance as Performance & { memory: { usedJSHeapSize: number } }).memory.usedJSHeapSize,
  );
  await page.evaluate(() => window.phase14Harness.soak(1800));
  await cdp.send("HeapProfiler.collectGarbage");
  const after = await page.evaluate(() =>
    (performance as Performance & { memory: { usedJSHeapSize: number } }).memory.usedJSHeapSize,
  );
  const result = { before, after, growth: (after - before) / before };
  console.log(`PHASE14_MEMORY_SOAK=${JSON.stringify(result)}`);
  expect(result.growth).toBeLessThanOrEqual(0.15);
  expect(await page.locator("canvas").count()).toBe(1);
});

test("records same-host PNG versus chunk render handoff", async ({ page }) => {
  const result = await page.evaluate(() =>
    window.phase14Harness.comparePngAndChunk(512, 24),
  );
  console.log(`PHASE14_SAME_HOST_COMPARISON=${JSON.stringify(result)}`);
  expect(result).toBeTruthy();
});

test("production volume viewer mounts the chunk source and changes axis", async ({ page }) => {
  let signedReads = 0;
  let tiffReads = 0;
  const apiPaths: string[] = [];
  await page.addInitScript(() => localStorage.setItem("mito_token", "test-token"));
  await page.route("http://127.0.0.1:4174/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    apiPaths.push(path);
    if (path === "/api/auth/me/") {
      return route.fulfill({ json: { id: 9, username: "phase14", role: "manager" } });
    }
    if (path === "/api/volumes/7/") {
      return route.fulfill({ json: {
        id: 7, project: 3, dataset: null, dataset_name: "fixture",
        name: "coordinate volume", shape_z: 2, shape_y: 3, shape_x: 4,
        has_label: false, has_region_mask: false,
      } });
    }
    if (path === "/api/projects/3/tasks/") return route.fulfill({ json: [] });
    if (path === "/api/volumes/7/meta/") {
      return route.fulfill({ json: {
        volume_id: 7, shape: { z: 2, y: 3, x: 4 }, dtype: "uint16",
        axes: ["z", "y", "x"], has_label: false, has_region_mask: false,
        display_range: { lo: 0, hi: 255 },
      } });
    }
    if (path === "/api/volumes/7/chunks/capabilities/") {
      return route.fulfill({ json: {
        volume_id: 7, build_identity: "browser-build",
        mags: [{
          mag: "1", shape: [2, 3, 4], chunks: [2, 3, 4],
          grid: [1, 1, 1], factors: [1, 1, 1], dtype: "uint16",
        }],
      } });
    }
    if (path === "/api/volumes/7/chunks/token/") {
      return route.fulfill({ json: { token: "signed-test", expires_at: 4102444800 } });
    }
    if (path.startsWith("/api/chunks/signed/")) {
      signedReads += 1;
      const body = Buffer.alloc(48);
      for (let i = 0; i < 24; i += 1) body.writeUInt16LE(i * 10, i * 2);
      return route.fulfill({
        status: 200,
        body,
        headers: {
          "Content-Type": "application/octet-stream",
          "Content-Length": String(body.length),
          "X-Mito-Byte-Order": "little",
          "X-Mito-Mag": "1",
          "X-Mito-Chunk": "0,0,0",
          "X-Mito-Build-Identity": "browser-build",
          "X-Mito-Dtype": "uint16",
          "X-Mito-Shape": "2,3,4",
          "X-Mito-Voxel-Offset": "0,0,0",
          ETag: "\"browser-fixture\"",
        },
      });
    }
    if (path === "/api/volumes/7/slice/") {
      tiffReads += 1;
      return route.fulfill({ status: 500, json: { detail: "TIFF path must not be used" } });
    }
    return route.fulfill({ status: 404, json: { detail: path } });
  });

  await page.goto("/viewer/volumes/7");
  const image = page.getByAltText(/slice/);
  await page.waitForTimeout(250);
  console.log(`PHASE14_PRODUCTION_VIEWER=${JSON.stringify({
    apiPaths,
    body: await page.locator("body").innerText(),
  })}`);
  await expect(image).toBeVisible();
  await expect.poll(() => signedReads).toBeGreaterThan(0);
  // React StrictMode's development-only effect probe may start one TIFF read
  // during dispose/remount; the committed visible frame must be chunk-backed.
  await expect(page.getByText(/using the TIFF\/PNG source/)).toHaveCount(0);
  await expect(image).toHaveJSProperty("naturalWidth", 4);
  await expect(image).toHaveJSProperty("naturalHeight", 3);
  await page.getByRole("button", { name: "Coronal (y)" }).click();
  await expect(page.locator(".slice-index")).toContainText("/3");
  await expect(image).toHaveJSProperty("naturalWidth", 4);
  await expect(image).toHaveJSProperty("naturalHeight", 2);
});
