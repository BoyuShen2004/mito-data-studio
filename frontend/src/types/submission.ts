import type { QCStatus, ReviewDecision } from "./index";
import type { AnnotationTask } from "./task";

export interface ReviewRecord {
  id: number;
  submission: number;
  reviewer: number | null;
  reviewer_username: string;
  decision: ReviewDecision;
  source: SubmissionSource;
  comments: string;
  reviewed_at: string;
}

export type SubmissionSource = "upload" | "inapp";

export interface Submission {
  id: number;
  task: number;
  task_detail: AnnotationTask;
  annotator: number | null;
  annotator_username: string;
  label_file: string;
  source: SubmissionSource;
  review_status: "pending" | "approved" | "rejected" | "revision_requested" | "voided";
  superseded_reason: string;
  notes: string;
  qc_status: QCStatus;
  qc_report: Record<string, unknown>;
  reviews: ReviewRecord[];
  round_number: number;
  superseded_at: string | null;
  is_current: boolean;
  submitted_at: string;
}
