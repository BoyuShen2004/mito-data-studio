import type { LabelType } from "./index";

export interface Volume {
  id: number;
  project: number;
  dataset: number | null;
  dataset_name: string;
  name: string;
  ready_streaming?: boolean;
  streaming_status?: "not_built" | "building" | "ready" | "failed";
  streaming_error?: string;
  pyramid_job_id?: number | null;
  // The region mask is a second read-only derivative with its own readiness.
  // "absent" means the volume has no ROI at all, which is not "unbuilt".
  region_ready_streaming?: boolean;
  region_streaming_status?: "absent" | "not_built" | "building" | "ready" | "failed";
  region_streaming_error?: string;
  region_pyramid_job_id?: number | null;
  image_path: string;
  image_file: string | null;
  region_mask_path: string;
  region_mask_file: string | null;
  label_path: string;
  label_file: string | null;
  label_type: LabelType;
  shape_z: number | null;
  shape_y: number | null;
  shape_x: number | null;
  voxel_size_z: number | null;
  voxel_size_y: number | null;
  voxel_size_x: number | null;
  file_format: string;
  metadata: Record<string, unknown>;
  status: string;
  has_label: boolean;
  has_region_mask: boolean;
  region_mask_coverage?: number | null;
  region_mask_empty?: boolean | null;
  image_location: string;
  region_mask_location: string;
  label_location: string;
  created_at: string;
}
