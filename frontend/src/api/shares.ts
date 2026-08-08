import { api } from "./client";

export interface PublicShare {
  id: number; token: string; scope: "project" | "dataset" | "volume";
  project_id: number; project_title: string; dataset_id: number | null; dataset_name: string;
  volume_id: number | null; volume_name: string; created_at: string; revoked_at: string | null; url: string;
  created_by: number | null; created_by_username: string;
}
export type AggregateShareState = "all" | "partial" | "none";
export interface ShareVolumeNode { id: number; name: string; has_region_mask?: boolean; region_mask_coverage?: number | null; shared: boolean; direct_shares: PublicShare[]; }
export interface ShareDatasetNode { id: number; name: string; state: AggregateShareState; direct_shares: PublicShare[]; volumes: ShareVolumeNode[]; }
export interface ShareProjectNode { id: number; title: string; state: AggregateShareState; direct_shares: PublicShare[]; datasets: ShareDatasetNode[]; ungrouped_volumes: ShareVolumeNode[]; }
export interface PublicShareTree { projects: ShareProjectNode[]; stop_policy: "direct_scope_only"; }
export interface EntityShareState { scope: PublicShare["scope"]; active: boolean; aggregate_state: AggregateShareState | "shared" | "not_shared"; shares: PublicShare[]; }
export interface PublicShareBrowse extends PublicShare {
  datasets: Array<{id: number; name: string; description: string}>;
  volumes: Array<{id: number; dataset_id: number | null; name: string; shape: Array<number | null>; voxel_size: Array<number | null>; file_format: string; label_type: string; has_region_mask?: boolean; region_mask_coverage?: number | null}>;
}
export const getPublicShareTree = () => api.get<PublicShareTree>("/public-shares/tree/");
export const createPublicShare = (body: {scope: string; project_id: number; dataset_id?: number; volume_id?: number}) => api.post<PublicShare>("/public-shares/", body);
export const revokePublicShare = (id: number) => api.post<PublicShare>(`/public-shares/${id}/revoke/`, {});
export const getEntityShare = (params: {scope: PublicShare["scope"]; project_id: number; dataset_id?: number; volume_id?: number}) => {
  const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value != null).map(([key, value]) => [key, String(value)]));
  return api.get<EntityShareState>(`/public-shares/entity/?${query.toString()}`);
};
export const getPublicShare = (token: string) => api.get<PublicShareBrowse>(`/public/shares/${encodeURIComponent(token)}/`);
