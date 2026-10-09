import { lazy } from "react";
import type { ProjectExtensionContext, ProjectExtensionDefinition } from "./types";

/** Register trusted, repository-owned extensions here. No extension is loaded
 * merely by listing it; the host mounts only the selected enabled component. */
export const projectExtensions: readonly ProjectExtensionDefinition[] = [
  {
    id: "measurements",
    title: "Measurements",
    description: "Measure saved mitochondrial labels: voxel count, physical volume, skeleton cable length and CSV export.",
    accessDescription: ({ isManager }) => isManager
      ? "You can save voxel size, start runs, inspect results and export CSV."
      : "You can inspect results and export CSV. Managers save voxel size and start runs.",
    enabled: true,
    legacyTabs: ["measurements"],
    component: lazy(() => import("./measurements/extension")),
  },
];

export const availableProjectExtensions = (context: ProjectExtensionContext, definitions = projectExtensions) =>
  definitions.filter(extension => extension.enabled && (!extension.isVisible || extension.isVisible(context)));

export const legacyProjectExtension = (tab: string | null) =>
  projectExtensions.find(extension => tab != null && extension.legacyTabs?.includes(tab));

export const projectExtensionSearch = (id: string, params: Record<string, string> = {}) => {
  const search = new URLSearchParams(params);
  search.set("tab", "extensions");
  search.set("extension", id);
  return search;
};

export const projectExtensionHref = (projectId: number, id: string, params: Record<string, string> = {}) =>
  `/projects/${projectId}?${projectExtensionSearch(id, params)}`;

/** Optional domain shortcuts disappear when their extension is disabled. */
export const isProjectExtensionEnabled = (id: string) =>
  projectExtensions.some(extension => extension.id === id && extension.enabled);
