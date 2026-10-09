import type { ComponentType, LazyExoticComponent } from "react";
import type { AsyncState } from "../../hooks/useAsync";
import type { Project } from "../../types/project";
import type { Volume } from "../../types/volume";

/** Shared project inputs; extensions own their forms, requests and results.
 * isManager guides UI controls. Every backend action must authorize separately. */
export interface ProjectExtensionContext {
  project: Project;
  isManager: boolean;
  volumes: AsyncState<Volume[]>;
}

export interface ProjectExtensionDefinition {
  /** Stable URL identifier; keep it when renaming a display title. */
  id: string;
  title: string;
  description: string;
  accessDescription: (context: ProjectExtensionContext) => string;
  /** Deployment-time opt-in. Disabling UI leaves scientific data/jobs intact. */
  enabled: boolean;
  legacyTabs?: readonly string[];
  isVisible?: (context: ProjectExtensionContext) => boolean;
  component: LazyExoticComponent<ComponentType<ProjectExtensionContext>>;
}
