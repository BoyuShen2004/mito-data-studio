import { expect, test, type Page, type Request } from "@playwright/test";

const username = process.env.MITO_STAGING_TEST_USERNAME;
const password = process.env.MITO_STAGING_TEST_PASSWORD;
const expectChunkRenderer = process.env.MITO_STAGING_EXPECT_CHUNK_RENDERER === "1";
const restoredTaskId = Number(process.env.MITO_STAGING_WORKER_B_TASK_ID);
const restoredVolumeId = Number(process.env.MITO_STAGING_WORKER_B_VOLUME_ID);

async function login(
  page: Page,
  credentials: { username?: string; password?: string } = { username, password },
  expectedHome: RegExp = /\/manager$/,
) {
  if (!credentials.username || !credentials.password) {
    throw new Error("Protected staging test credentials were not supplied");
  }
  await page.goto("/login");
  await page.getByRole("tab", { name: "Annotator Login" }).click();
  await page.getByLabel("Username").fill(credentials.username);
  await page.getByLabel("Password").fill(credentials.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(expectedHome);
}

test("restored release exposes manager collaboration workflows", async ({ page }) => {
  await login(page);

  await page.goto("/projects");
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await expect(page.locator("tbody tr").first()).toBeVisible();

  await page.goto("/people");
  await expect(page.getByRole("heading", { name: "People" })).toBeVisible();

  await page.goto("/hard-cases");
  await expect(page.getByRole("heading", { name: "Hard Cases" })).toBeVisible();
  await expect(page.getByText(/^Open \([1-9][0-9]*\)$/)).toBeVisible();
});

test("integrated organization, team, assignment, review, and region workflows", async ({ browser }) => {
  test.skip(
    process.env.MITO_STAGING_INTEGRATED_WORKFLOWS !== "1",
    "Explicit restored-data fixture opt-in only",
  );
  test.setTimeout(360_000);
  const manager = {
    username: process.env.MITO_STAGING_TEST_USERNAME,
    password: process.env.MITO_STAGING_TEST_PASSWORD,
  };
  const annotator = {
    username: process.env.MITO_STAGING_ASSIGNEE_USERNAME,
    password: process.env.MITO_STAGING_ASSIGNEE_PASSWORD,
  };
  const assignedTaskId = Number(process.env.MITO_STAGING_ASSIGNED_TASK_ID);
  const projectId = Number(process.env.MITO_STAGING_WORKFLOW_PROJECT_ID);
  const regionTaskId = Number(process.env.MITO_STAGING_REGION_TASK_ID);
  const organization = process.env.MITO_STAGING_WORKFLOW_ORGANIZATION;
  const team = process.env.MITO_STAGING_WORKFLOW_TEAM;
  expect([assignedTaskId, projectId, regionTaskId].every(Number.isSafeInteger)).toBe(true);
  expect(organization).toBeTruthy();
  expect(team).toBeTruthy();

  const managerContext = await browser.newContext();
  const annotatorContext = await browser.newContext();
  const managerPage = await managerContext.newPage();
  const annotatorPage = await annotatorContext.newPage();
  await Promise.all([
    login(managerPage, manager),
    login(annotatorPage, annotator, /\/annotator$/),
  ]);

  // The existing public SPA stays intact; the integrated collaboration data
  // is additionally operable through the established Manager Admin.
  await managerPage.goto("/admin/login/?next=/admin/");
  if (await managerPage.getByLabel("Username:").count()) {
    await managerPage.getByLabel("Username:").fill(manager.username as string);
    await managerPage.getByLabel("Password:").fill(manager.password as string);
    await managerPage.getByRole("button", { name: "Log in" }).click();
    await expect(managerPage).not.toHaveURL(/\/admin\/login\//);
  }
  for (const [path, expected] of [
    ["/admin/accounts/institution/", organization],
    ["/admin/accounts/team/", team],
    ["/admin/accounts/teammembership/", annotator.username],
    ["/admin/accounts/auditevent/", "Project access granted to team"],
  ] as const) {
    await managerPage.goto(path);
    await expect(managerPage.getByText(expected as string, { exact: false }).first())
      .toBeVisible();
  }

  // Auto-fill remains a manager-side draft until saved. The annotator then
  // opens the one task already pushed to them through My Tasks.
  await managerPage.goto(`/projects/${projectId}`);
  await managerPage.getByRole("button", { name: "Auto-fill balanced plan" }).click();
  await expect(managerPage.getByText(/Plan filled|Created [0-9]+ new task/))
    .toBeVisible({ timeout: 30_000 });

  await annotatorPage.goto("/annotator");
  const assignedRow = annotatorPage.getByRole("row").filter({
    hasText: `#${assignedTaskId}`,
  });
  await expect(assignedRow).toBeVisible();
  await assignedRow.getByRole("button", { name: "Annotate" }).click();
  await expect(annotatorPage).toHaveURL(new RegExp(`/editor/tasks/${assignedTaskId}$`));
  await expect(annotatorPage.getByRole("heading", { name: `Annotate · Task #${assignedTaskId}` }))
    .toBeVisible();
  const overlay = annotatorPage.locator(".canvas-stage > canvas").first();
  await expect(overlay).toBeVisible({ timeout: 120_000 });
  await annotatorPage.locator(".tool-fieldset").getByRole("button", { name: "Brush", exact: true }).click();
  await annotatorPage.getByRole("button", { name: "New", exact: true }).click();
  const box = await overlay.boundingBox();
  if (!box) throw new Error("annotation overlay has no browser layout box");
  await overlay.click({ position: { x: box.width / 2, y: box.height / 2 } });
  const save = annotatorPage.getByRole("button", { name: "Save", exact: true });
  await expect(save).toBeEnabled();
  await save.click();
  await expect(annotatorPage.locator(".tool-strip-status"))
    .toHaveText("Saved", { timeout: 60_000 });
  await annotatorPage.getByRole("button", { name: "Submit for review" }).click();
  await expect(annotatorPage.getByText(/Submitted for review/))
    .toBeVisible({ timeout: 30_000 });
  const submissionId = await annotatorPage.evaluate(async (taskId) => {
    const token = localStorage.getItem("mito_token");
    const response = await fetch("/api/submissions/?task_status=submitted", {
      headers: token ? { Authorization: `Token ${token}` } : {},
    });
    if (!response.ok) throw new Error(`submission list failed: ${response.status}`);
    const rows = await response.json() as Array<{ id: number; task: number }>;
    const row = rows.find((candidate) => candidate.task === taskId);
    if (!row) throw new Error("staged submitted task is absent from the review queue");
    return row.id;
  }, assignedTaskId);

  await managerPage.goto(`/submissions/${submissionId}/review`);
  await expect(managerPage.getByRole("heading", { name: `Review submission #${submissionId}` }))
    .toBeVisible();
  await managerPage.getByLabel("Comments").fill("v1.1 integrated staging review");
  await managerPage.getByRole("button", { name: "Request revision" }).click();
  await expect(managerPage).toHaveURL(/\/manager$/);

  await managerPage.goto(`/viewer/tasks/${regionTaskId}`);
  const regionLayer = managerPage.locator('.canvas-stage > img[aria-hidden="true"]');
  await expect(regionLayer).toBeVisible({ timeout: 60_000 });
  await expect.poll(() => regionLayer.evaluate((node: HTMLImageElement) => node.naturalWidth))
    .toBeGreaterThan(0);

  await managerPage.goto("/people");
  await expect(managerPage.getByText(annotator.username as string, { exact: false }).first())
    .toBeVisible();
  await Promise.all([managerContext.close(), annotatorContext.close()]);
});

test("reserved second annotator opens the assigned public verification task", async ({ page }) => {
  test.skip(
    process.env.MITO_STAGING_PUBLIC_ASSIGNMENT !== "1",
    "Explicit post-cutover synthetic assigned task only",
  );
  const worker = {
    username: process.env.MITO_STAGING_WORKER_B_USERNAME,
    password: process.env.MITO_STAGING_WORKER_B_PASSWORD,
  };
  const taskId = Number(process.env.MITO_STAGING_WORKER_B_TASK_ID);
  expect(Number.isSafeInteger(taskId)).toBe(true);
  await login(page, worker, /\/annotator$/);
  const assignedRow = page.getByRole("row").filter({ hasText: `#${taskId}` });
  await expect(assignedRow).toBeVisible();
  await assignedRow.getByRole("button", { name: "Annotate" }).click();
  await expect(page).toHaveURL(new RegExp(`/editor/tasks/${taskId}$`));
  await expect(page.getByRole("heading", { name: `Annotate · Task #${taskId}` }))
    .toBeVisible();
});

test("staging full-task share is anonymously readable and has no write route", async ({ page }) => {
  await login(page);
  const shareToken = await page.evaluate(async (taskId) => {
    const token = localStorage.getItem("mito_token");
    const response = await fetch(`/api/tasks/${taskId}/share/`, {
      method: "POST",
      headers: token ? { Authorization: `Token ${token}` } : {},
    });
    if (!response.ok) throw new Error(`share creation failed: ${response.status}`);
    return String((await response.json()).token);
  }, restoredTaskId);
  await page.evaluate(() => localStorage.removeItem("mito_token"));
  await page.goto(`/share/task/${encodeURIComponent(shareToken)}`);
  await expect(page.getByText("READ-ONLY · NO ACCOUNT NEEDED")).toBeVisible();
  await expect(page.locator(".canvas-stage > img").first()).toBeVisible();
  const writeStatus = await page.evaluate(async (token) => {
    const response = await fetch(`/api/public/tasks/${encodeURIComponent(token)}/label-ids/`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ axis: "z", index: 0, shape: [1, 1], runs: [[0, 1]] }),
    });
    return response.status;
  }, shareToken);
  expect(writeStatus).toBe(405);
});

test("disabled chunk renderer keeps the restored TIFF axis path", async ({ page }) => {
  test.skip(expectChunkRenderer, "This assertion belongs to the initial TIFF build");
  const paths: string[] = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/api/")) paths.push(url.pathname);
  });
  await login(page);
  await page.goto(`/viewer/volumes/${restoredVolumeId}`);
  await expect(page.getByRole("heading", { name: /^View ·/ })).toBeVisible();

  const image = page.locator(".canvas-stage > img").first();
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.naturalWidth))
    .toBeGreaterThan(0);

  const axis = page.getByLabel("Axis");
  for (const value of ["y", "x", "z"] as const) {
    await axis.selectOption(value);
    await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.naturalWidth))
      .toBeGreaterThan(0);
  }

  expect(paths.some((path) => path === `/api/volumes/${restoredVolumeId}/slice/`)).toBe(true);
  expect(paths.some((path) => path.includes("/chunks/"))).toBe(false);
  await expect(page.getByText(/falling back to TIFF/i)).toHaveCount(0);
});

test("enabled chunk renderer serves real restored XY XZ and YZ data", async ({ page }) => {
  test.skip(!expectChunkRenderer, "Chunk renderer is not expected in this build");
  const paths: string[] = [];
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (path.startsWith("/api/")) paths.push(path);
  });
  await login(page);
  await page.goto("/viewer/volumes/3");
  const image = page.locator(".canvas-stage > img").first();
  await expect(image).toBeVisible();
  await expect.poll(() => paths.filter((path) => path.startsWith("/api/chunks/signed/")).length, {
    timeout: 30_000,
  }).toBeGreaterThan(0);

  const axis = page.getByLabel("Axis");
  for (const value of ["y", "x", "z"] as const) {
    await axis.selectOption(value);
    await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.naturalWidth))
      .toBeGreaterThan(0);
  }

  expect(paths).toContain("/api/volumes/3/chunks/capabilities/");
  expect(paths).toContain("/api/volumes/3/chunks/token/");
  expect(paths.some((path) => path.startsWith("/api/chunks/signed/"))).toBe(true);
  await expect(page.getByText(/falling back to TIFF/i)).toHaveCount(0);
});

test("expired chunk authorization refreshes without falling back", async ({ page }) => {
  test.skip(!expectChunkRenderer, "Chunk renderer is not expected in this build");
  let signedAttempts = 0;
  let tokenRequests = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.endsWith("/chunks/token/")) tokenRequests += 1;
  });
  await page.route("**/api/chunks/signed/**", async (route) => {
    signedAttempts += 1;
    if (signedAttempts === 1) {
      await route.fulfill({ status: 401, json: { detail: "injected expiry" } });
    } else {
      await route.continue();
    }
  });
  await login(page);
  await page.goto("/viewer/volumes/3");
  await expect.poll(() => signedAttempts, { timeout: 30_000 }).toBeGreaterThan(1);
  expect(tokenRequests).toBeGreaterThan(1);
  await expect(page.locator(".canvas-stage > img").first()).toBeVisible();
  await expect(page.getByText(/falling back to TIFF/i)).toHaveCount(0);
});

test("malformed chunk fails closed and falls back to TIFF", async ({ page }) => {
  test.skip(!expectChunkRenderer, "Chunk renderer is not expected in this build");
  let tiffReads = 0;
  page.on("response", (response) => {
    if (new URL(response.url()).pathname === "/api/volumes/3/slice/") tiffReads += 1;
  });
  await page.route("**/api/chunks/signed/**", (route) => route.fulfill({
    status: 200,
    contentType: "application/octet-stream",
    body: "not-a-valid-chunk",
  }));
  await login(page);
  await page.goto("/viewer/volumes/3");
  await expect(page.getByText(/falling back to TIFF|using the TIFF\/PNG source/i))
    .toBeVisible({ timeout: 30_000 });
  await expect.poll(() => tiffReads).toBeGreaterThan(0);
  const fallbackImage = page.locator(".canvas-stage > img").first();
  await expect.poll(
    () => fallbackImage.evaluate((node: HTMLImageElement) => node.naturalWidth),
    { timeout: 30_000 },
  ).toBeGreaterThan(0);
});

test("records real staging target-slice visible latency", async ({ page }) => {
  test.skip(process.env.MITO_STAGING_BENCHMARK !== "1", "Explicit benchmark run only");
  const source = process.env.MITO_STAGING_SOURCE ?? "unknown";
  let bytes = 0;
  let requests = 0;
  page.on("response", async (response) => {
    const path = new URL(response.url()).pathname;
    if (path.includes("/chunks/") || path.endsWith("/slice/")) {
      requests += 1;
      const length = Number(response.headers()["content-length"] ?? 0);
      if (Number.isFinite(length)) bytes += length;
    }
  });
  await login(page);
  await page.goto("/viewer/volumes/3");
  const image = page.locator(".canvas-stage > img").first();
  const slider = page.locator(".canvas-controls input[type=range]").first();
  await expect(image).toBeVisible();
  await expect(slider).toBeVisible();

  const samples: number[] = [];
  for (let index = 1; index <= 20; index += 1) {
    const previous = await image.getAttribute("src");
    const started = performance.now();
    await slider.fill(String(index));
    await expect.poll(() => image.getAttribute("src"), { timeout: 30_000 })
      .not.toBe(previous);
    samples.push(performance.now() - started);
  }
  const sorted = [...samples].sort((a, b) => a - b);
  const percentile = (fraction: number) =>
    sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * fraction) - 1)];
  const result = {
    source,
    samples: sorted.length,
    p50: percentile(0.5),
    p95: percentile(0.95),
    p99: percentile(0.99),
    requests,
    bytes,
  };
  console.log(`STAGING_VISIBLE_LATENCY=${JSON.stringify(result)}`);
  // Measurement guard only. Release readiness applies the authoritative
  // viewport p95 gate to this recorded value; a miss keeps the renderer flag
  // disabled rather than making the evidence harness unable to finish.
  expect(result.p99).toBeLessThan(10_000);
});

test("runs the configurable concurrent-user navigation soak", async ({ browser }) => {
  const durationSeconds = Number(process.env.MITO_STAGING_SOAK_SECONDS ?? 0);
  const warmupSeconds = Number(process.env.MITO_STAGING_SOAK_WARMUP_SECONDS ?? 60);
  test.skip(durationSeconds <= 0, "Set MITO_STAGING_SOAK_SECONDS (7200 for the gate)");
  // Keep teardown outside the measured interval. Long-running TIFF requests
  // can take a while to abort and Chromium must still have time to close its
  // contexts and flush Playwright's result channel deterministically.
  test.setTimeout((durationSeconds + warmupSeconds + 600) * 1000);
  const credentials = [
    {
      username: process.env.MITO_STAGING_WORKER_A_USERNAME,
      password: process.env.MITO_STAGING_WORKER_A_PASSWORD,
    },
    {
      username: process.env.MITO_STAGING_WORKER_B_USERNAME,
      password: process.env.MITO_STAGING_WORKER_B_PASSWORD,
    },
  ];
  const contexts = await Promise.all([browser.newContext(), browser.newContext()]);
  const pages = await Promise.all(contexts.map((context) => context.newPage()));
  const failures: string[] = [];
  const responseLatencies: number[] = [];
  const statusCounts: Record<string, number> = {};
  const requestKinds: Record<string, number> = {};
  const requestFailureCounts: Record<string, number> = {};
  let cancelledRequests = 0;
  for (const page of pages) {
    const requestStartedAt = new Map<Request, number>();
    page.on("pageerror", (error) => failures.push(error.message));
    page.on("request", (request) => requestStartedAt.set(request, performance.now()));
    page.on("requestfailed", (request) => {
      requestStartedAt.delete(request);
      const reason = request.failure()?.errorText ?? "unknown";
      requestFailureCounts[reason] = (requestFailureCounts[reason] ?? 0) + 1;
      if (reason.includes("ERR_ABORTED")) cancelledRequests += 1;
      else failures.push(`request failed: ${reason} ${new URL(request.url()).pathname}`);
    });
    page.on("response", (response) => {
      const status = String(response.status());
      statusCounts[status] = (statusCounts[status] ?? 0) + 1;
      const path = new URL(response.url()).pathname;
      const kind = path.includes("/chunks/") ? "chunk"
        : path.endsWith("/slice/") ? "tiff_slice"
          : path.includes("/label-ids/") ? "label_ids" : "other";
      requestKinds[kind] = (requestKinds[kind] ?? 0) + 1;
      const request = response.request();
      const startedAt = requestStartedAt.get(request);
      if (startedAt !== undefined) responseLatencies.push(performance.now() - startedAt);
      requestStartedAt.delete(request);
      if (response.status() >= 500) failures.push(`${response.status()} ${response.url()}`);
    });
  }
  const soakHome = process.env.MITO_STAGING_SOAK_HOME ?? "/manager";
  const volumeIds = [
    Number(process.env.MITO_STAGING_WORKER_A_VOLUME_ID ?? 1),
    Number(process.env.MITO_STAGING_WORKER_B_VOLUME_ID ?? 3),
  ];
  const taskIds = [
    process.env.MITO_STAGING_WORKER_A_TASK_ID,
    process.env.MITO_STAGING_WORKER_B_TASK_ID,
  ];
  const viewerPaths = volumeIds.map((volumeId, index) =>
    taskIds[index] ? `/viewer/tasks/${taskIds[index]}` : `/viewer/volumes/${volumeId}`,
  );
  await Promise.all(pages.map((page, index) =>
    login(page, credentials[index], new RegExp(`${soakHome.replace("/", "\\/")}$`)),
  ));
  await Promise.all([
    pages[0].goto(viewerPaths[0]),
    pages[1].goto(viewerPaths[1]),
  ]);
  await Promise.all(pages.map((page) =>
    expect(page.locator(".canvas-stage > img").first()).toBeVisible(),
  ));

  const sessions = await Promise.all(
    pages.map((page, index) => contexts[index].newCDPSession(page)),
  );
  await Promise.all(sessions.map((session) => session.send("Performance.enable")));

  let cycles = 0;
  const renderLatencies: number[] = [];
  const runCycle = async () => {
    const axis = (["z", "y", "x"] as const)[cycles % 3];
    const started = performance.now();
    await Promise.all(pages.map(async (page, index) => {
      const image = page.locator(".canvas-stage > img").first();
      const axisControl = page.getByLabel("Axis");
      const previous = await image.getAttribute("src");
      const slider = page.locator(".canvas-controls input[type=range]").first();
      const maximum = Number(await slider.getAttribute("max"));
      const currentIndex = Number(await slider.inputValue());
      const span = Math.max(1, maximum + 1);
      const targetIndex = span > 1 ? (currentIndex + 1 + index) % span : currentIndex;
      const changesViewport = (await axisControl.inputValue()) !== axis || targetIndex !== currentIndex;
      const response = changesViewport
        ? page.waitForResponse((candidate) => {
          const path = new URL(candidate.url()).pathname;
          return path.endsWith("/slice/") && candidate.status() === 200;
        }, { timeout: 30_000 })
        : Promise.resolve(null);
      await axisControl.selectOption(axis);
      await slider.fill(String(targetIndex));
      await response;
      await expect(image).toBeVisible();
      if (previous && changesViewport) {
        await expect.poll(() => image.getAttribute("src"), { timeout: 30_000 })
          .not.toBe(previous);
      }
    }));
    renderLatencies.push(performance.now() - started);
    cycles += 1;
    if (cycles % 12 === 0) {
      await Promise.all(pages.map((page) => page.goto(soakHome)));
      await Promise.all([
        pages[0].goto(viewerPaths[0]),
        pages[1].goto(viewerPaths[1]),
      ]);
    }
  };

  // Warm every route, axis, decoder and browser allocation before defining a
  // retained-heap baseline. The old harness measured lazy initialization as a
  // leak because it sampled immediately after first paint.
  const warmupDeadline = Date.now() + warmupSeconds * 1000;
  while (Date.now() < warmupDeadline) await runCycle();

  type MemorySample = {
    elapsedMs: number;
    heaps: Array<{ used: number; total: number; limit: number }>;
    eventLoopLagMs: number[];
    cdp: Array<Record<string, number>>;
  };
  const samples: MemorySample[] = [];
  const startedAt = Date.now();
  const sample = async () => {
    // Route mounting is deliberately part of the workload, but manager and
    // editor pages have very different retained heaps. Sampling whichever
    // route happens to be active turns that periodic phase difference into a
    // fictitious leak. Return both users to their fixed editor route before
    // every GC/sample so first, middle and last measurements are comparable.
    await Promise.all(pages.map(async (page, index) => {
      if (new URL(page.url()).pathname !== viewerPaths[index]) {
        await page.goto(viewerPaths[index]);
      }
      await expect(page.locator(".canvas-stage > img").first()).toBeVisible();
    }));
    await Promise.all(sessions.map((session) => session.send("HeapProfiler.collectGarbage")));
    const heaps = await Promise.all(pages.map((page) => page.evaluate(() => {
      const memory = (performance as Performance & {
        memory: { usedJSHeapSize: number; totalJSHeapSize: number; jsHeapSizeLimit: number };
      }).memory;
      return { used: memory.usedJSHeapSize, total: memory.totalJSHeapSize, limit: memory.jsHeapSizeLimit };
    })));
    const eventLoopLagMs = await Promise.all(pages.map((page) => page.evaluate(() =>
      new Promise<number>((resolve) => {
        const started = performance.now();
        setTimeout(() => resolve(performance.now() - started), 0);
      }),
    )));
    const cdp = await Promise.all(sessions.map(async (session) => {
      const result = await session.send("Performance.getMetrics") as {
        metrics: Array<{ name: string; value: number }>;
      };
      return Object.fromEntries(result.metrics.map(({ name, value }) => [name, value]));
    }));
    samples.push({ elapsedMs: Date.now() - startedAt, heaps, eventLoopLagMs, cdp });
  };
  await sample();

  const deadline = Date.now() + durationSeconds * 1000;
  let nextSample = Date.now() + 30_000;
  while (Date.now() < deadline) {
    await runCycle();
    if (Date.now() >= nextSample) {
      await sample();
      nextSample = Date.now() + 30_000;
    }
  }
  await sample();
  const first = samples[0].heaps;
  const last = samples[samples.length - 1].heaps;
  const growth = last.map((heap, index) => (heap.used - first[index].used) / first[index].used);
  const percentile = (values: number[], fraction: number) => {
    const sorted = [...values].sort((a, b) => a - b);
    return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * fraction) - 1))]
      ?? 0;
  };
  const quarter = Math.max(1, Math.floor(samples.length / 4));
  const firstBand = samples.slice(0, quarter);
  const lastBand = samples.slice(-quarter);
  // Route mounting and TIFF decode have a repeatable low/high retained-heap
  // cycle. Comparing band medians aliases that phase and can call a perfectly
  // stable upper envelope a leak. A leak raises the p90/max envelope and DOM
  // population, so gate those directly (plus an absolute per-page cap).
  const envelopeGrowth = first.map((_heap, index) => {
    const baseline = percentile(firstBand.map((entry) => entry.heaps[index].used), 0.9);
    const final = percentile(lastBand.map((entry) => entry.heaps[index].used), 0.9);
    return baseline > 0 ? (final - baseline) / baseline : 0;
  });
  const maxHeap = first.map((_heap, index) =>
    Math.max(...samples.map((entry) => entry.heaps[index].used)));
  const nodeEnvelopeGrowth = first.map((_heap, index) => {
    const baseline = Math.max(...firstBand.map((entry) => entry.cdp[index].Nodes ?? 0));
    const final = Math.max(...lastBand.map((entry) => entry.cdp[index].Nodes ?? 0));
    return baseline > 0 ? (final - baseline) / baseline : 0;
  });
  const sortedResponse = [...responseLatencies].sort((a, b) => a - b);
  const sortedRender = [...renderLatencies].sort((a, b) => a - b);
  console.log(`STAGING_MULTIUSER_SOAK=${JSON.stringify({
    requestedSeconds: durationSeconds,
    warmupSeconds,
    cycles,
    users: pages.length,
    samples,
    growth,
    envelopeGrowth,
    maxHeap,
    nodeEnvelopeGrowth,
    responseLatencyMs: {
      p50: percentile(sortedResponse, 0.5),
      p95: percentile(sortedResponse, 0.95),
      p99: percentile(sortedResponse, 0.99),
    },
    renderLatencyMs: {
      p50: percentile(sortedRender, 0.5),
      p95: percentile(sortedRender, 0.95),
      p99: percentile(sortedRender, 0.99),
    },
    statusCounts,
    requestKinds,
    requestFailureCounts,
    cancelledRequests,
    failures,
  })}`);
  // Stop decoders and navigation before closing the contexts. Closing a page
  // while large TIFF responses are still being decoded can leave context.close
  // waiting until the test-level timeout, which also corrupts failure-artifact
  // collection. Detach CDP first, navigate to an inert document, then close.
  await Promise.all(sessions.map((session) => session.detach().catch(() => undefined)));
  await Promise.all(pages.map((page) =>
    page.goto("about:blank", { waitUntil: "commit", timeout: 10_000 }).catch(() => null),
  ));
  await Promise.all(pages.map((page) => page.close({ runBeforeUnload: false })));
  await Promise.all(contexts.map((context) => context.close()));
  expect(failures).toEqual([]);
  expect(requestKinds.chunk ?? 0).toBe(0);
  for (const value of envelopeGrowth) expect(value).toBeLessThanOrEqual(0.25);
  for (const value of maxHeap) expect(value).toBeLessThanOrEqual(256 * 1024 * 1024);
  for (const value of nodeEnvelopeGrowth) expect(value).toBeLessThanOrEqual(0.1);
});

test("restored annotators preserve distinct-task edits and expose all tool surfaces", async ({ browser }) => {
  test.skip(
    process.env.MITO_STAGING_SOAK_WORKFLOWS !== "1",
    "Explicit restored-data write opt-in only",
  );
  test.setTimeout(360_000);
  const workers = [
    {
      username: process.env.MITO_STAGING_WORKER_A_USERNAME,
      password: process.env.MITO_STAGING_WORKER_A_PASSWORD,
      taskId: Number(process.env.MITO_STAGING_WORKER_A_TASK_ID),
      otherTaskId: Number(process.env.MITO_STAGING_WORKER_B_TASK_ID),
    },
    {
      username: process.env.MITO_STAGING_WORKER_B_USERNAME,
      password: process.env.MITO_STAGING_WORKER_B_PASSWORD,
      taskId: Number(process.env.MITO_STAGING_WORKER_B_TASK_ID),
      otherTaskId: Number(process.env.MITO_STAGING_WORKER_A_TASK_ID),
    },
  ];
  expect(workers.every((worker) => Number.isSafeInteger(worker.taskId))).toBe(true);
  const distinctVolumeIds = [
    Number(process.env.MITO_STAGING_WORKER_A_VOLUME_ID),
    Number(process.env.MITO_STAGING_WORKER_B_VOLUME_ID),
  ];
  expect(distinctVolumeIds.every(Number.isSafeInteger)).toBe(true);
  expect(distinctVolumeIds[0]).not.toBe(distinctVolumeIds[1]);
  const contexts = await Promise.all(workers.map(() => browser.newContext()));
  const pages = await Promise.all(contexts.map((context) => context.newPage()));
  await Promise.all(pages.map((page, index) =>
    login(page, workers[index], /\/annotator$/),
  ));

  const digest = (page: Page, taskId: number) => page.evaluate(async (id) => {
    const token = localStorage.getItem("mito_token");
    const response = await fetch(`/api/tasks/${id}/label-ids/?axis=z&index=0`, {
      headers: token ? { Authorization: `Token ${token}` } : {},
    });
    if (!response.ok) throw new Error(`label read failed: ${response.status}`);
    const bytes = new TextEncoder().encode(JSON.stringify(await response.json()));
    const hash = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(hash), (value) => value.toString(16).padStart(2, "0")).join("");
  }, taskId);

  const preserved: Array<{ before: string; after: string; reloaded: string }> = [];
  await Promise.all(pages.map(async (page, index) => {
    const worker = workers[index];
    const denied = await page.evaluate(async ({ taskId }) => {
      const token = localStorage.getItem("mito_token");
      const response = await fetch(`/api/tasks/${taskId}/label-ids/`, {
        method: "PUT",
        headers: {
          ...(token ? { Authorization: `Token ${token}` } : {}),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          axis: "z", index: 0, shape: [1, 1], runs: [[0, 1]], origin: "manual",
        }),
      });
      return response.status;
    }, { taskId: worker.otherTaskId });
    expect(denied).toBe(403);

    await page.goto(`/editor/tasks/${worker.taskId}`);
    await expect(page.getByRole("heading", { name: new RegExp(`Task #${worker.taskId}$`) })).toBeVisible();
    const overlay = page.locator(".canvas-stage > canvas").first();
    await expect(overlay).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("heading", { name: "Track (SAM2)" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Labels" }).first()).toBeVisible();
    for (const tool of ["Erase", "Box Erase", "Box Mask", "Point Mask", "Brush"]) {
      await page.locator(".tool-fieldset").getByRole("button", { name: tool, exact: true }).click();
    }

    const before = await digest(page, worker.taskId);
    await page.getByRole("button", { name: "New", exact: true }).click();
    const box = await overlay.boundingBox();
    if (!box) throw new Error("annotation overlay has no browser layout box");
    await overlay.click({ position: { x: box.width * (0.4 + index * 0.2), y: box.height / 2 } });
    await page.getByRole("button", { name: "Undo", exact: true }).click();
    await page.getByRole("button", { name: "Redo", exact: true }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator(".tool-strip-status")).toHaveText("Saved", { timeout: 60_000 });
    const after = await digest(page, worker.taskId);
    expect(after).not.toBe(before);
    await page.reload();
    // The largest restored production mask can take well over 30 seconds to
    // remap and rebuild its label inventory after a cold reload.
    await expect(page.locator(".canvas-stage > canvas").first()).toBeVisible({ timeout: 120_000 });
    const reloaded = await digest(page, worker.taskId);
    expect(reloaded).toBe(after);
    preserved.push({ before, after, reloaded });
  }));

  console.log(`STAGING_DISTINCT_TASK_EDITS=${JSON.stringify({ attempted: 2, preserved: preserved.length })}`);
  await Promise.all(contexts.map((context) => context.close()));
});

test("same-task stale write is a controlled 409 with no mask change", async () => {
  test.skip(true, "Removed with Phase 10 autosave/recovery (expected_version path deleted)");
});

test("staging brush Save and reload persist exact working-mask bytes", async ({ page }) => {
  test.skip(
    process.env.MITO_STAGING_ALLOW_WRITES !== "1",
    "Explicit opt-in protects any accidentally targeted non-staging service",
  );
  await login(page);
  const writePaths: string[] = [];
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    if (["POST", "PUT"].includes(response.request().method())) writePaths.push(path);
  });
  await page.goto(`/editor/tasks/${restoredTaskId}`);
  await expect(page.getByRole("heading", { name: `Annotate · Task #${restoredTaskId}` })).toBeVisible();

  const image = page.locator(".canvas-stage > img").first();
  const overlay = page.locator(".canvas-stage > canvas").first();
  await expect(image).toBeVisible();
  await expect(overlay).toBeVisible();

  const labelDigest = () => page.evaluate(async (taskId) => {
    const token = localStorage.getItem("mito_token");
    const response = await fetch(`/api/tasks/${taskId}/label-ids/?axis=z&index=0`, {
      headers: token ? { Authorization: `Token ${token}` } : {},
    });
    if (!response.ok) throw new Error(`label slice failed: ${response.status}`);
    const digest = await crypto.subtle.digest("SHA-256", await response.arrayBuffer());
    return Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");
  }, restoredTaskId);
  const before = await labelDigest();

  await page.locator(".tool-fieldset").getByRole("button", { name: "Brush", exact: true }).click();
  await page.getByRole("button", { name: "New", exact: true }).click();
  const box = await overlay.boundingBox();
  if (!box) throw new Error("annotation overlay has no browser layout box");
  await overlay.click({ position: { x: box.width / 2, y: box.height / 2 } });

  const save = page.getByRole("button", { name: "Save", exact: true });
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page.locator(".tool-strip-status")).toHaveText("Saved", { timeout: 30_000 });
  expect(writePaths).toContain(`/api/tasks/${restoredTaskId}/label-ids/`);
  expect(writePaths.some((path) => path.includes("/autosave/"))).toBe(false);

  const after = await labelDigest();
  expect(after).not.toBe(before);
  await page.reload();
  await expect(page.getByRole("heading", { name: `Annotate · Task #${restoredTaskId}` })).toBeVisible();
  await expect.poll(labelDigest).toBe(after);
});

test("browser autosave retries one injected transport failure without edit loss", async () => {
  test.skip(true, "Removed with Phase 10 autosave/recovery");
});

test("enabled EfficientSAM and SAM2 execute through the restored browser workflow", async ({ page }) => {
  test.skip(
    process.env.MITO_STAGING_AI_WORKFLOWS !== "1",
    "Explicit restored-data AI write opt-in only",
  );
  test.setTimeout(600_000);
  await login(page, {
    username: process.env.MITO_STAGING_WORKER_B_USERNAME,
    password: process.env.MITO_STAGING_WORKER_B_PASSWORD,
  }, /\/annotator$/);
  await page.goto(`/editor/tasks/${restoredTaskId}`);
  const overlay = page.locator(".canvas-stage > canvas").first();
  await expect(overlay).toBeVisible({ timeout: 120_000 });
  const box = await overlay.boundingBox();
  if (!box) throw new Error("annotation overlay has no browser layout box");

  await page.getByRole("button", { name: "Point Mask", exact: true }).click();
  const prediction = page.waitForResponse((response) =>
    new URL(response.url()).pathname === `/api/tasks/${restoredTaskId}/predict-mask/`,
    { timeout: 180_000 },
  );
  await overlay.click({ position: { x: box.width / 2, y: box.height / 2 } });
  expect((await prediction).status()).toBe(200);
  await page.getByRole("button", { name: "Clear (Esc)", exact: true }).click();

  // Give SAM2 a small, deterministic child-class prompt on the current
  // production-data plane, then exercise the queued batch path.
  await page.locator(".tool-fieldset").getByRole("button", { name: "Brush", exact: true }).click();
  await page.getByRole("button", { name: "New", exact: true }).click();
  await overlay.click({ position: { x: box.width / 2, y: box.height / 2 } });
  await page.getByLabel("Track z from").fill("0");
  await page.getByLabel("Track z to").fill("0");
  await page.getByRole("button", { name: /^Add parent class \d+ to queue$/ }).click();
  await page.locator(".track-prompt-tools").getByRole("button", { name: "Brush", exact: true }).click();
  const promptOverlay = page.getByLabel("SAM tracking prompt overlay");
  const promptSaved = page.waitForResponse((response) =>
    response.request().method() === "PUT" &&
    new URL(response.url()).pathname === `/api/tasks/${restoredTaskId}/track/prompts/`,
  );
  await promptOverlay.click({ position: { x: box.width / 2, y: box.height / 2 } });
  expect((await promptSaved).status()).toBe(200);
  const tracking = page.waitForResponse((response) =>
    new URL(response.url()).pathname === `/api/tasks/${restoredTaskId}/track/batch/`,
    { timeout: 420_000 },
  );
  await page.getByRole("button", { name: "Propagate selected", exact: true }).click();
  expect((await tracking).status()).toBe(200);
  const confirmed = page.waitForResponse((response) =>
    new URL(response.url()).pathname === `/api/tasks/${restoredTaskId}/track/review/`,
  );
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  expect((await confirmed).status()).toBe(200);
});
