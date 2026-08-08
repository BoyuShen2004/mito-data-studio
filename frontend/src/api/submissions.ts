import type { ReviewDecision } from "../types";
import type { Submission } from "../types/submission";
import { api } from "./client";

export const submitTask = (taskId: number, form: FormData) =>
  api.postForm<Submission>(`/tasks/${taskId}/submit/`, form);

// In-app editor submission — no file to upload, the working label copy
// already lives server-side (see backend/annotation/label_paths.py).
export const submitInappTask = (taskId: number, notes = "") =>
  api.post<Submission>(`/tasks/${taskId}/submit-inapp/`, { notes });

export const listSubmissions = (taskStatus?: string) =>
  api.get<Submission[]>(
    `/submissions/${taskStatus ? `?task_status=${taskStatus}` : ""}`,
  );

export const getSubmission = (id: number) =>
  api.get<Submission>(`/submissions/${id}/`);

/** `allowFurtherAnnotation` only applies to `approved`: off (the default)
 * locks the task, on lets the annotator keep editing and submit again.
 * Reject / revision always reopen it. */
export const reviewSubmission = (
  id: number,
  decision: ReviewDecision,
  comments: string,
  allowFurtherAnnotation = false,
) =>
  api.post<{ review_id: number; submission: Submission }>(
    `/submissions/${id}/review/`,
    {
      decision,
      comments,
      allow_further_annotation: allowFurtherAnnotation,
    },
  );
