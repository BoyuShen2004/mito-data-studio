import type { DatasetMetadata } from "../types/project";
import { api } from "./client";

/** A dataset inside a project. A project holds many; each holds many volumes. */
export interface Dataset {
  id: number;
  project: number;
  project_title: string;
  name: string;
  description: string;
  image_directory: string;
  region_mask_directory: string;
  mask_directory: string;
  metadata: DatasetMetadata;
  volume_count: number;
  created_at: string;
}

export interface DatasetInput {
  project?: number;
  name?: string;
  description?: string;
  image_directory?: string;
  region_mask_directory?: string;
  mask_directory?: string;
  metadata?: DatasetMetadata;
}

/** What a delete would take with it. */
export interface Dependents {
  volumes: number;
  tasks: number;
  submissions: number;
  reviews: number;
  datasets?: number;
}

export const listDatasets = (projectId?: number) =>
  api.get<Dataset[]>(
    projectId ? `/datasets/?project=${projectId}` : "/datasets/",
  );

export const createDataset = (data: DatasetInput) =>
  api.post<Dataset>("/datasets/", data);

export const updateDataset = (id: number, data: DatasetInput) =>
  api.patch<Dataset>(`/datasets/${id}/`, data);

export const datasetDependents = (id: number) =>
  api.get<Dependents>(`/datasets/${id}/dependents/`);

/** Deleting is refused (409) while annotation work exists unless `force`. */
export const deleteDataset = (id: number, force = false) =>
  api.del<{ deleted: Dependents }>(`/datasets/${id}/${force ? "?force=true" : ""}`);

export const projectDependents = (id: number) =>
  api.get<Dependents>(`/projects/${id}/dependents/`);

export const deleteProjectForce = (id: number, force = false) =>
  api.del<{ deleted: Dependents }>(`/projects/${id}/${force ? "?force=true" : ""}`);
