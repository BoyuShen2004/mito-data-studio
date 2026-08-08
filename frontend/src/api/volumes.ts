import type { Volume } from "../types/volume";
import { api } from "./client";

export const listProjectVolumes = (projectId: number) =>
  api.get<Volume[]>(`/projects/${projectId}/volumes/`);

export const getVolume = (id: number) => api.get<Volume>(`/volumes/${id}/`);

export const registerVolume = (projectId: number, form: FormData) =>
  api.postForm<Volume>(`/projects/${projectId}/volumes/`, form);

export const updateVolume = (id: number, form: FormData) =>
  api.patchForm<Volume>(`/volumes/${id}/`, form);

/** Edit a volume's fields (name, paths, label type, dataset) as JSON. */
export interface VolumeEdit {
  name?: string;
  image_path?: string;
  region_mask_path?: string;
  label_path?: string;
  label_type?: string;
  dataset?: number;
}

export const editVolume = (id: number, data: VolumeEdit) =>
  api.patch<Volume>(`/volumes/${id}/`, data);

export const volumeDependents = (id: number) =>
  api.get<import("./datasets").Dependents>(`/volumes/${id}/dependents/`);

/** Refused (409) while the volume has tasks/submissions unless `force`. */
export const deleteVolume = (id: number, force = false) =>
  api.del<{ deleted: import("./datasets").Dependents }>(
    `/volumes/${id}/${force ? "?force=true" : ""}`,
  );

/** Queue a build of one read-only layer's pyramid (image by default). */
export const buildVolumePyramid = (id: number, layer: "image" | "region" = "image") =>
  api.post<{ job_id: number; volume: Volume }>(`/volumes/${id}/pyramid/`, { layer });
