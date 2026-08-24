import type { SubmissionSource } from "./submission";

export interface ReviewLabelComment {
  id: number;
  submission: number;
  task: number;
  label_id: number;
  body: string;
  /** Plane where the manager left the comment; null on older rows. */
  view_z: number | null;
  view_y: number | null;
  view_x: number | null;
  view_axis: string;
  author: number | null;
  author_username: string;
  project_title: string;
  volume_name: string;
  /** Task frame range (not the comment camera). */
  z_start: number;
  z_end: number;
  round_number: number;
  submission_source: SubmissionSource;
  submission_review_status: string;
  created_at: string;
  updated_at: string;
}
