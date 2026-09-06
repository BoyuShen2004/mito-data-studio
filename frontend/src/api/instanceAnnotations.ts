import { api } from "./client";
import type {
  InstanceAnnotation,
  InstanceAnnotationPatch,
  InstanceAnnotationResponse,
  InstanceSummary,
} from "../types/instanceAnnotation";

export function fetchTaskInstanceAnnotations(
  taskId: number,
): Promise<InstanceAnnotationResponse> {
  return api.get(`/tasks/${taskId}/instance-annotations/`);
}

export function fetchVolumeInstanceAnnotations(
  volumeId: number,
): Promise<InstanceAnnotationResponse> {
  return api.get(`/volumes/${volumeId}/instance-annotations/`);
}

/**
 * Upsert one instance. An omitted field is left unchanged server-side, so the
 * panel can send a single control's value without clearing the others.
 */
export function saveInstanceAnnotation(
  taskId: number,
  labelId: number,
  patch: InstanceAnnotationPatch,
): Promise<InstanceAnnotation> {
  return api.put(`/tasks/${taskId}/instance-annotations/${labelId}/`, patch);
}

export function fetchProjectInstanceSummary(
  projectId: number,
): Promise<InstanceSummary> {
  return api.get(`/projects/${projectId}/instance-annotations/summary/`);
}

/** Index annotations by label id, for O(1) lookup while rendering a list. */
export function byLabelId(
  annotations: InstanceAnnotation[],
): Map<number, InstanceAnnotation> {
  return new Map(annotations.map((row) => [row.label_id, row]));
}
