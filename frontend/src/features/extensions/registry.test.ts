import { expect, it } from "vitest";
import { availableProjectExtensions, legacyProjectExtension, projectExtensionHref, projectExtensionSearch, projectExtensions } from "./registry";
import type { ProjectExtensionContext } from "./types";
const context = { project: { id: 4 }, isManager: false } as ProjectExtensionContext;

it("uses unique stable IDs and noncolliding legacy tab aliases", () => {
  const ids = projectExtensions.map(extension => extension.id);
  expect(new Set(ids).size).toBe(ids.length);
  for (const id of ids) expect(id).toMatch(/^[a-z][a-z0-9-]*$/);
  const aliases = projectExtensions.flatMap(extension => extension.legacyTabs ?? []);
  expect(new Set(aliases).size).toBe(aliases.length);
  expect(legacyProjectExtension("measurements")?.id).toBe("measurements");
  expect(legacyProjectExtension("tasks")).toBeUndefined();
});
it("keeps Measurements available to project viewers and filters disabled or hidden entries", () => {
  expect(availableProjectExtensions(context).map(extension => extension.id)).toContain("measurements");
  const base = projectExtensions[0];
  expect(availableProjectExtensions(context, [{ ...base, enabled: false }])).toEqual([]);
  expect(availableProjectExtensions(context, [{ ...base, isVisible: ({ isManager }) => isManager }])).toEqual([]);
});
it("keeps extension links scoped and serializes volume inputs", () => {
  const url = new URL(projectExtensionHref(4, "measurements", { volume: "17" }), "http://localhost");
  expect(url.pathname).toBe("/projects/4");
  expect(url.searchParams.get("tab")).toBe("extensions");
  expect(url.searchParams.get("extension")).toBe("measurements");
  expect(url.searchParams.get("volume")).toBe("17");
  expect(projectExtensionSearch("measurements", { tab: "tasks", extension: "other" }).get("tab")).toBe("extensions");
});
