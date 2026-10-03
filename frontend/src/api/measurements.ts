import { api } from "./client";

export type MeasurementSource = "official" | "working";
export interface MitoMeasurement {
  label_id: number;
  voxel_count: number;
  volume_um3: number;
  skeleton_length_um: number;
}
export interface MeasurementResult {
  rows: MitoMeasurement[];
  measured_at: string;
  source: MeasurementSource;
  voxel_size_nm_zyx: [number, number, number];
  dust_size_voxels: number;
  method: string;
  scope: string;
}
export interface MeasurementJob {
  id: number;
  source: MeasurementSource;
  status: "queued" | "submitted" | "running" | "succeeded" | "failed" | "cancelled";
  created_at: string;
  finished_at: string | null;
  error: string;
  is_current: boolean;
  result: MeasurementResult | null;
}
export const getMeasurements = (volume: number, source: MeasurementSource) =>
  api.get<{ job: MeasurementJob | null }>(`/volumes/${volume}/measurements/?source=${source}`);
export const runMeasurements = (volume: number, source: MeasurementSource) =>
  api.post<{ job: MeasurementJob }>(`/volumes/${volume}/measurements/`, { source });

export function measurementCSV(volume: number, job: MeasurementJob): string {
  const result = job.result;
  if (!result) return "";
  const header = "volume_id,run_id,source,measured_at,voxel_size_z_nm,voxel_size_y_nm,voxel_size_x_nm,dust_size_voxels,label_id,voxel_count,volume_um3,skeleton_length_um";
  return [header, ...result.rows.map(row => [
    volume, job.id, result.source, result.measured_at, ...result.voxel_size_nm_zyx,
    result.dust_size_voxels, row.label_id, row.voxel_count, row.volume_um3, row.skeleton_length_um,
  ].join(","))].join("\n") + "\n";
}
