import type { ReviewDecision, TaskStatus, TaskType } from "./index";

import type { DatasetMetadata } from "./project";
import type { AnnotationTimeSummary } from "../api/timing";

export interface AnnotationTask {
  id: number;
  project: number;
  project_title: string;
  dataset: string;
  // The shared biomedical metadata (same source managers/requesters see).
  dataset_metadata: DatasetMetadata;
  voxel_size_z: number | null;
  voxel_size_y: number | null;
  voxel_size_x: number | null;
  shape_z: number | null;
  shape_y: number | null;
  shape_x: number | null;
  file_format: string;
  volume_status: string;
  ready_streaming?: boolean;
  streaming_status?: "not_built" | "building" | "ready" | "failed";
  streaming_error?: string;
  region_ready_streaming?: boolean;
  region_streaming_status?: "absent" | "not_built" | "building" | "ready" | "failed";
  region_streaming_error?: string;
  has_region_mask: boolean;
  volume: number;
  volume_name: string;
  image_location: string;
  region_mask_location: string;
  region_mask_coverage?: number | null;
  label_location: string;
  /** Volume mask kind: none | partial | prediction (legacy: proofread). */
  label_type: string;
  assigned_to: number | null;
  assigned_to_username: string;
  z_start: number;
  z_end: number;
  y_start: number;
  y_end: number;
  x_start: number;
  x_end: number;
  task_type: TaskType;
  review_history: Array<{id: number; round_number: number; annotator_username: string; submitted_at: string; superseded_at: string | null; superseded_reason: string; source: "upload" | "inapp"; review_status: "pending" | "approved" | "rejected" | "revision_requested" | "voided"; reviews: Array<{id: number; decision: string; source: "upload" | "inapp"; comments: string; reviewer_username: string; reviewed_at: string}>}>;
  status: TaskStatus;
  priority: number;
  difficulty: number;
  instructions: string;
  deadline: string | null;
  frame_label: string;
  /** Manager approved without "allow further annotation" — paint + Submit are
   * closed (API 403s, not just hidden UI). */
  annotation_locked: boolean;
  /** Server-decided Submit gate. Never re-derive this from `status` on the
   * client — that is exactly what made Submit vanish after the first submit. */
  can_submit: boolean;
  /** Server-decided paint gate (edit access AND not locked). */
  can_annotate: boolean;
  /** Cumulative measured annotation time.
   *
   *  `tracked: false` means the volume is legacy-exempt — its annotation began
   *  before time tracking existed, so the real total is unknown and must render
   *  as `-`, never as `0m`. `display` already carries the right string. */
  annotation_time: AnnotationTimeSummary;
  /** How many times this task has been handed over for review. */
  submission_count: number;
  last_decision: "" | ReviewDecision;
  last_decision_at: string | null;
  last_decision_by: number | null;
  last_decision_by_username: string;
  last_decision_comments: string;
  last_decision_source: "" | "upload" | "inapp";
  created_at: string;
  assigned_at: string | null;
  submitted_at: string | null;
  approved_at: string | null;
  history_key?: string;
  assignment_withdrawn?: boolean;
  assignment_transferred?: boolean;
  withdrawal_reason?: string;
  withdrawal_team?: string;
  transferred_to?: number | null;
  transferred_to_username?: string;
  withdrawn_at?: string;
}

export interface Annotator {
  id: number;
  username: string;
  is_active_annotator: boolean;
  max_active_tasks: number;
}

// A row of the draft assignment plan: a task plus the annotator the auto-planner
// proposes for it, which the manager can override before saving.
export type AssignmentPlanTask = Pick<AnnotationTask,
  "id" | "project" | "volume" | "volume_name" | "label_type" |
  "assigned_to" | "assigned_to_username" | "z_start" | "z_end" |
  "task_type" | "status" | "priority" | "difficulty" | "instructions" |
  "deadline" | "annotation_locked" | "annotation_time"
> & {
  file_format: string;
  shape_z: number | null;
  shape_y: number | null;
  shape_x: number | null;
  voxel_size_z: number | null;
  voxel_size_y: number | null;
  voxel_size_x: number | null;
  has_region_mask: boolean;
  region_mask_coverage: number | null;
};

export interface PlanEntryTask extends AssignmentPlanTask {
  proposed_annotator_id: number | null;
}

export interface AssignmentPlanPreview {
  created_tasks: number;
  skipped_volumes: number;
  entries: PlanEntryTask[];
}

// Response of GET-ing the plan editor's rows: one per volume (a task is
// created for any volume that doesn't have one yet), with no proposed
// annotator — that's only computed when "Auto-fill balanced plan" runs.
export interface AssignmentPlanRows {
  created_tasks: number;
  skipped_volumes: number;
  entries: AssignmentPlanTask[];
}

// What the client sends back when saving. Only task_id is required; other keys
// are included when the manager edited them.
export interface PlanEntryInput {
  task_id: number;
  annotator_id?: number | null;
  priority?: number;
  difficulty?: number;
  instructions?: string;
  deadline?: string | null;
}

export interface ApplyPlanResult {
  updated: number;
  assigned: number;
  remaining_unassigned: number;
}
