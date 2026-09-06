export type MilestoneStatus = "planned" | "active" | "met" | "missed";
export type MilestoneMetric = "tasks_approved" | "volumes_completed";

export interface MilestoneProgress {
  id: number;
  name: string;
  due_on: string;
  status: MilestoneStatus;
  target_metric: MilestoneMetric;
  target_value: number;
  achieved: number;
  remaining: number;
  /** null when no target is set — never render this as "complete". */
  percent_complete: number | null;
  days_remaining: number;
  overdue: boolean;
  scoped_volumes: number;
}

export interface Milestone {
  id: number;
  project: number;
  name: string;
  description: string;
  due_on: string;
  order: number;
  status: MilestoneStatus;
  volumes: number[];
  target_metric: MilestoneMetric;
  target_value: number;
  completed_at: string | null;
  created_at: string;
  progress: MilestoneProgress;
}

export interface ThroughputPoint {
  date: string;
  approved: number;
}

export interface BurndownPoint {
  date: string;
  remaining: number;
}

export interface ProductivityRow {
  user_id: number;
  username: string;
  tasks_assigned: number;
  tasks_approved: number;
  tasks_submitted: number;
  mean_review_rounds: number | null;
  mean_elapsed_to_submit_seconds: number | null;
  /** null = unknowable (legacy-exempt volumes). 0 = measured as none. */
  annotated_seconds: number | null;
}

export interface AttentionTask {
  id: number;
  volume: string;
  project: string;
  deadline: string | null;
  status: string;
  assigned_to: string | null;
}

export interface StaleReview {
  id: number;
  task: number;
  volume: string;
  annotator: string | null;
  submitted_at: string;
}

export interface ProjectDelivery {
  throughput: { start: string; end: string; points: ThroughputPoint[] };
  productivity: { count: number; results: ProductivityRow[] };
  attention: {
    overdue: { count: number; results: AttentionTask[] };
    due_soon: { count: number; results: AttentionTask[] };
    stale_reviews: { count: number; results: StaleReview[] };
    thresholds: { soon_days: number; stale_days: number };
  };
}
