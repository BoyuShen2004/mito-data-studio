import { expect, test, type Page } from "@playwright/test";
import { installScientificFixture, visualTask, visualProject, visualVolume } from "./fixtures/scientific-workbench";

async function signInFixture(page: Page) {
  await page.addInitScript(() => localStorage.setItem("mito_token", "isolated-visual-fixture"));
}

for (const size of [{ width: 1440, height: 900 }, { width: 1280, height: 800 }]) {
  test(`scientific context, usable targets, and stable canvas at ${size.width}px`, async ({ page }, info) => {
    await page.setViewportSize(size);
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await installScientificFixture(page);
    await signInFixture(page);
    await page.goto("/editor/tasks/42");
    await expect(page.getByRole("heading", { name: visualTask.volume_name })).toBeVisible();
    await expect(page.locator(".viewer-identity")).toContainText(visualTask.dataset);
    const context = page.getByLabel("Scientific context");
    await expect(context).toContainText("Z 1/64");
    await expect(context).toContainText("Label #1");
    await expect(context).toContainText("Editable");
    await expect(page.locator(".canvas-stage img")).toBeVisible();
    const viewport = page.locator(".canvas-viewport");
    const before = await viewport.boundingBox();
    expect(before!.height).toBeGreaterThan(size.height * 0.6);
    for (const name of ["Select", "Brush", "Save", "Submit for review"]) {
      const box = await page.getByRole("button", { name, exact: true }).first().boundingBox();
      expect(box!.height).toBeGreaterThanOrEqual(32);
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(size.width);
    }
    await page.getByRole("button", { name: "Brush", exact: true }).first().click();
    await expect(context).toContainText("Brush");
    await expect(page.getByRole("button", { name: "Brush", exact: true }).first()).toHaveAttribute("aria-pressed", "true");
    const after = await viewport.boundingBox();
    expect(Math.abs(after!.height - before!.height)).toBeLessThan(2);
    // Sharing the row with a wider tool context must not clip its controls.
    const cursor = await page.getByLabel("Cursor", { exact: true }).boundingBox();
    const contextRow = await page.locator(".tool-context").boundingBox();
    expect(cursor!.y).toBeGreaterThanOrEqual(contextRow!.y);
    expect(cursor!.y + cursor!.height).toBeLessThanOrEqual(contextRow!.y + contextRow!.height);
    await page.getByTitle("Next layer", { exact: true }).click();
    await expect(context).toContainText("Z 2/64");
    await page.getByRole("button", { name: "View only", exact: true }).click();
    await expect(context).toContainText("Read-only");
    await expect(page.getByRole("button", { name: "Save", exact: true })).toHaveCount(0);
    expect(errors).toEqual([]);
    await page.screenshot({ path: info.outputPath("viewer.png") });
  });
}

test("working edits, Save, and Submit expose separate states", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const fixture = await installScientificFixture(page);
  await signInFixture(page);
  await page.goto("/editor/tasks/42");
  await expect(page.locator(".canvas-stage img")).toBeVisible();
  await page.getByRole("button", { name: "Fit window", exact: true }).click();
  await page.getByRole("button", { name: "Brush", exact: true }).first().click();
  const viewport = await page.locator(".canvas-viewport").boundingBox();
  await page.mouse.click(viewport!.x + viewport!.width / 2, viewport!.y + viewport!.height / 2);
  await expect(page.locator(".tool-strip-status")).toHaveText("Unsaved");
  expect(fixture.writes).toHaveLength(0);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.locator(".tool-strip-status")).toHaveText("Saved");
  expect(fixture.writes).toHaveLength(1);
  await expect(page.locator(".viewer-workflow-state")).toHaveText("in progress");
  await page.getByRole("button", { name: "Submit for review", exact: true }).click();
  await expect(page.locator(".viewer-workflow-state")).toHaveText("submitted");
  await expect(page.locator(".tool-strip-status")).toHaveText("Saved");
});

test("compact data pages retain readable identities and unknown metadata", async ({ page }, info) => {
  await installScientificFixture(page);
  await signInFixture(page);
  for (const path of ["/?tab=mine", "/projects/3?tab=data", "/tasks/42"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath(`${path.startsWith('/?') ? 'home' : path.split('/')[1]}.png`), fullPage: true });
  }
  await page.goto("/projects/3?tab=data");
  await expect(page.getByRole("columnheader", { name: "Voxel size (Z × Y × X)" })).toBeVisible();
  await expect(page.locator("td.mono-cell").filter({ hasText: /^—$/ })).toHaveCount(1);
  // Half-sized CSS viewport exercises the same reflow constraints as 200% desktop zoom.
  await page.setViewportSize({ width: 720, height: 450 });
  await page.goto("/?tab=mine");
  await expect(page.locator(".work-row-state").first()).toHaveText("in progress");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("login keeps explicit sign-in and development helpers at narrow widths", async ({ page }, info) => {
  await installScientificFixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/login");
  await page.getByRole("button", { name: /demo_annotator/ }).click();
  await expect(page.getByLabel("Username", { exact: true })).toHaveValue("demo_annotator");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  const hint = await page.locator(".login-hint").boundingBox();
  const accounts = await page.locator(".dev-accounts").boundingBox();
  expect(accounts!.y).toBeGreaterThan(hint!.y + hint!.height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("login-mobile.png"), fullPage: true });
});

test("review retains submission identity and distinct decisions", async ({ page }, info) => {
  await installScientificFixture(page);
  await signInFixture(page);
  const round = {
    id: 501, round_number: 1, source: "inapp", review_status: "pending",
    annotator_username: "researcher", submitted_at: "2026-10-01T09:00:00Z",
    superseded_at: null, superseded_reason: "", reviews: [],
  };
  const task = { ...visualTask, status: "submitted", submission_count: 1, review_history: [round] };
  await page.route("**/api/tasks/42/", route => route.fulfill({ json: task }));
  await page.route("**/api/submissions/501/", route => route.fulfill({
    json: { ...round, task: 42, task_detail: task, qc_status: "passed", qc_report: {}, notes: "", label_comment_count: 0 },
  }));
  await page.goto("/tasks/42");
  await expect(page.getByRole("heading", { name: "Review this submission" })).toBeVisible();
  await expect(page.locator(".review-box")).toContainText("Round 1");
  await expect(page.getByRole("button", { name: "Approve & close", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Request revision", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reject", exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("review.png"), fullPage: true });
});

test("public viewer remains read-only without application navigation", async ({ page }, info) => {
  await installScientificFixture(page);
  await page.route("**/api/public/shares/visual/", route => route.fulfill({ json: {
    id: 1, token: "visual", scope: "volume", project_id: 3,
    project_title: visualProject.title, dataset_id: 1, volume_id: 9,
    datasets: visualProject.datasets,
    volumes: [{ ...visualVolume, dataset_id: 1, shape: [64, 256, 256], voxel_size: [null, null, null] }],
  } }));
  await page.goto("/share/public/visual");
  await expect(page.getByLabel("Scientific context")).toContainText("Read-only");
  await expect(page.locator(".canvas-stage img")).toBeVisible();
  await expect(page.locator(".navbar")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Submit for review", exact: true })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("public-viewer.png") });
});

test("volume measurements queue explicitly and show source, units and CSV export", async ({ page }, info) => {
  await installScientificFixture(page);
  await signInFixture(page);
  const requests: string[] = [];
  await page.route("**/api/volumes/9/", route => route.fulfill({ json: {
    ...visualVolume, voxel_size_z: 30, voxel_size_y: 16, voxel_size_x: 16,
    image_path: "image.tif", label_path: "labels.tif", region_mask_path: "",
  } }));
  let job: unknown = null;
  await page.route("**/api/volumes/9/measurements/**", async route => {
    if (route.request().method() === "POST") {
      requests.push(route.request().postDataJSON().source);
      job = {
        id: 100, source: "official", status: "succeeded", created_at: "2026-10-02T00:00:00Z",
        finished_at: "2026-10-02T00:01:00Z", is_current: true, error: "",
        result: {
          source: "official", measured_at: "2026-10-02T00:01:00Z", voxel_size_nm_zyx: [30, 16, 16],
          dust_size_voxels: 100, method: "TEASAR (kimimaro)", scope: "Whole volume",
          rows: [{ label_id: 5, voxel_count: 3600, volume_um3: 0.027648, skeleton_length_um: 1.6 }],
        },
      };
    }
    await route.fulfill({ json: { job } });
  });
  let spacing: Record<string, number> = {};
  await page.route("**/api/projects/3/volumes/", route => route.fulfill({ json: [{ ...visualVolume, ...spacing }] }));
  await page.route("**/api/volumes/9/", async route => {
    if (route.request().method() === "PATCH") spacing = route.request().postDataJSON();
    await route.fulfill({ json: { ...visualVolume, ...spacing, image_path: "image.tif", label_path: "labels.tif", region_mask_path: "" } });
  });
  await page.goto("/projects/3?tab=measurements&volume=9");
  await expect(page.getByRole("tab", { name: "Measurements" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("button", { name: "Run measurements" })).toBeDisabled();
  for (const [axis, value] of [["Z", "30"], ["Y", "16"], ["X", "16"]]) {
    await page.getByLabel(`${axis} (nm)`, { exact: true }).fill(value);
  }
  await page.getByRole("button", { name: "Save voxel size" }).click();
  await expect(page.getByRole("button", { name: "Run measurements" })).toBeEnabled();
  const section = page.getByRole("region", { name: "Mitochondria measurements" });
  await expect(section.getByText(/No measurements have been run/)).toBeVisible();
  expect(requests).toEqual([]);
  await section.getByRole("button", { name: "Run measurements" }).click();
  await expect(section.getByRole("table")).toBeVisible();
  await expect(section).toContainText("Volume (µm³)");
  await expect(section).toContainText("Skeleton cable length (µm)");
  expect(requests).toEqual(["official"]);
  const download = page.waitForEvent("download");
  await section.getByRole("button", { name: "Export CSV" }).click();
  expect((await download).suggestedFilename()).toBe("mitochondria-volume-9-official-run-100.csv");
  await page.screenshot({ path: info.outputPath("measurements.png"), fullPage: true });
});
