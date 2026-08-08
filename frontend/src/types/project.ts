import type { ProjectStatus, TaskStatus } from "./index";
import type { Lifecycle, WorkflowType } from "../labels";

export interface DatasetMetadata {
  organism?: string;
  tissue?: string;
  cell_type?: string;
  imaging_modality?: string;
  imaging_instrument?: string;
  experimental_condition?: string;
  sample_condition?: string;
  dataset_source?: string;
  publication?: string;
  description?: string;
  notes?: string;
  [key: string]: unknown;
}

export interface Project {
  id: number;
  title: string;
  /** Legacy single-dataset name; `datasets` is the real list. */
  dataset: string;
  datasets: import("../api/datasets").Dataset[];
  dataset_count: number;
  institution: number | null;
  institution_name: string;
  description: string;
  metadata: DatasetMetadata;
  annotation_target: string;
  annotation_type: string;
  workflow_type: WorkflowType;
  lifecycle: Lifecycle;
  status: ProjectStatus;
  priority: number;
  paused: boolean;
  teams: number[];
  working_team: number | null;
  deadline: string | null;
  created_by: number | null;
  created_by_username: string;
  manager_reviewed: boolean;
  reviewed_by: number | null;
  reviewed_by_username: string;
  reviewed_at: string | null;
  volume_count: number;
  task_count: number;
  created_at: string;
}

export interface ProjectProgress {
  total_tasks: number;
  approved_tasks: number;
  percent_complete: number;
  status_counts: Record<TaskStatus, number>;
  volumes: number;
}

export interface WorkloadRow {
  annotator_id: number;
  username: string;
  total: number;
  active: number;
  submitted: number;
  approved: number;
}

export interface ProjectSummary {
  project: Project;
  progress: ProjectProgress;
  // Manager-only detail; absent for requesters.
  workload?: WorkloadRow[];
}
