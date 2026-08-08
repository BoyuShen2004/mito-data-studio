// A hard case recorded against a project. Both audiences ride on one row: the
// in-app `app_url` for project members and the public `url`/`token` for the
// optional copyable link. Permissions (`can_annotate` / `can_take_down`) are
// decided server-side — see backend/annotation/services.py's matrix.

export type HardCaseStatus = "open" | "resolved";

export interface HardCase {
  id: number;
  token: string;
  task: number;
  task_status: string;
  project: number | null;
  project_title: string;
  volume: number | null;
  volume_name: string;
  label_id: number;
  z_start: number;
  z_end: number;
  status: HardCaseStatus;
  /** Public token link killed; project members are unaffected. */
  revoked: boolean;
  created_by: number | null;
  created_by_username: string;
  created_at: string;
  resolved_by: number | null;
  resolved_by_username: string;
  resolved_at: string | null;
  /** Public share path, e.g. "/share/hard-case/<token>" — prepend the origin. */
  url: string;
  /** In-app path for project members, e.g. "/hard-cases/12". */
  app_url: string;
  can_annotate: boolean;
  can_take_down: boolean;
}
