import type { Project, DatasetMetadata } from "../types/project";
import type { LabelType } from "../types";
import type { Volume } from "../types/volume";
import { api } from "./client";
import { listDatasets, type Dataset } from "./datasets";
import { listProjectVolumes } from "./volumes";

export interface HpcFile {
  name: string;
  path: string;
  extension: string;
  size: number;
}

/** An image matched to its mask by case id. The two may be in different dirs. */
export interface DetectedPair {
  image: string;
  region_mask?: string;
  mask: string;
  case: string;
}

/** A sibling folder offered as a quick pick (e.g. labelsTr vs labelsTr-instance). */
export interface DirSuggestion {
  name: string;
  path: string;
  count: number;
  split: string;
  current: boolean;
}

export interface ScanResult {
  image_directory: string;
  region_mask_directory: string;
  mask_directory: string;
  image_files: HpcFile[];
  region_mask_files: HpcFile[];
  mask_files: HpcFile[];
  pairs: DetectedPair[];
  region_by_image: Record<string, string>;
  unmatched_images: string[];
  unmatched_region_masks: string[];
  unmatched_masks: string[];
  extra_channels: string[];
  /** "dataset.json" when pairs came from the manifest, else "filename". */
  pairing_source: string;
  split: string;
  suggestions: { images: DirSuggestion[]; masks: DirSuggestion[] };
  dataset_metadata: Record<string, unknown>;
  manifest_path: string;
}

export const scanDataSources = (
  image_directory: string,
  mask_directory: string,
  region_mask_directory = "",
) =>
  api.post<ScanResult>("/hpc/scan/", {
    image_directory,
    mask_directory,
    region_mask_directory,
  });

export interface RegisterDataFile {
  name?: string;
  /** @deprecated Alias for ``name``; accepted by older clients only. */
  chunk_id?: string;
}

export interface RegisterDataPair {
  image: string;
  region_mask?: string;
  mask?: string;
  /** Optional display name for this volume (defaults to the case/file id). */
  name?: string;
  /** @deprecated Alias for ``name``. */
  chunk_id?: string;
}

export interface RegisterDataInput {
  dataset: string;
  image_directory: string;
  region_mask_directory?: string;
  mask_directory?: string;
  project?: number | null;
  annotation_type?: string;
  pairs?: RegisterDataPair[];
  files?: RegisterDataFile[];
  label_type?: LabelType;
  metadata?: DatasetMetadata;
}

export interface RegisterDataResult {
  project: Project;
  volumes: Volume[];
}

export const registerData = (data: RegisterDataInput) =>
  api.post<RegisterDataResult>("/register-data/", data);

export interface RegistrationSnapshot {
  dataset: Dataset | null;
  volumeIds: number[];
}

export type RegistrationReconciliation =
  | { status: "complete"; volumes: Volume[] }
  | { status: "missing" }
  | { status: "inconclusive" };

/** Capture the target dataset before POSTing so a lost response can be
 * reconciled against newly-created volumes rather than an older dataset that
 * happens to have the same name. Dataset names are unique within a project. */
export async function getRegistrationSnapshot(
  projectId: number,
  datasetName: string,
): Promise<RegistrationSnapshot | null> {
  try {
    const [datasets, volumes] = await Promise.all([
      listDatasets(projectId),
      listProjectVolumes(projectId),
    ]);
    const dataset = datasets.find((row) => row.name === datasetName) ?? null;
    return {
      dataset,
      volumeIds: dataset
        ? volumes.filter((volume) => volume.dataset === dataset.id).map((volume) => volume.id)
        : [],
    };
  } catch {
    // Registration should still be attempted if this defensive snapshot is
    // unavailable. Reconciliation can use the post-request state alone.
    return null;
  }
}

/** Verify whether an interrupted registration completed in the database.
 * This intentionally checks only registration facts; pyramid readiness is an
 * asynchronous derivative and is never part of registration success. */
export async function reconcileRegistration(
  projectId: number,
  datasetName: string,
  expectedVolumeCount: number,
  before: RegistrationSnapshot | null,
  options: {attempts?: number; delayMs?: number} = {},
): Promise<RegistrationReconciliation> {
  const attempts = Math.max(1, options.attempts ?? 4);
  const delayMs = Math.max(0, options.delayMs ?? 1500);
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const [datasets, volumes] = await Promise.all([
        listDatasets(projectId),
        listProjectVolumes(projectId),
      ]);
      const dataset = datasets.find((row) => row.name === datasetName);
      if (dataset) {
        const datasetVolumes = volumes.filter((volume) => volume.dataset === dataset.id);
        if (!before) {
          // Without a trustworthy pre-request state, an existing same-name
          // dataset cannot prove that this particular POST created anything.
          return { status: "inconclusive" };
        }
        const priorIds = new Set(
          before.dataset?.id === dataset.id ? before.volumeIds : [],
        );
        const registeredVolumes = datasetVolumes.filter(
          (volume) => !priorIds.has(volume.id),
        );
        if (registeredVolumes.length >= expectedVolumeCount) {
          return {
            status: "complete",
            // A concurrent registration could add more rows. The caller needs
            // only the expected rows for the ordinary success result.
            volumes: registeredVolumes.slice(-expectedVolumeCount),
          };
        }
      }
      // The ingress connection can disappear before the still-running worker
      // commits its last rows. Give that worker a short grace window before a
      // successfully-refetched absence becomes a definitive failure.
      if (attempt < attempts - 1 && delayMs > 0) {
        await new Promise((resolve) => window.setTimeout(resolve, delayMs));
      }
    } catch {
      return { status: "inconclusive" };
    }
  }
  return { status: "missing" };
}
