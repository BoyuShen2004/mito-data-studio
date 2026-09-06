import { api } from "./client";
import type {
  AnnotatorQuality,
  ProjectQuality,
  QualityScore,
} from "../types/quality";

/**
 * Scores for one submission.
 *
 * Resolves to an empty list rather than rejecting when quality metrics are
 * disabled (503): the review page must still render without them.
 */
export async function fetchSubmissionQuality(
  submissionId: number,
): Promise<QualityScore[]> {
  try {
    const result = await api.get<{ results: QualityScore[] }>(
      `/submissions/${submissionId}/quality/`,
    );
    return result.results;
  } catch {
    return [];
  }
}

export function fetchProjectQuality(projectId: number): Promise<ProjectQuality> {
  return api.get(`/projects/${projectId}/quality/`);
}

export function fetchAnnotatorQuality(
  username: string,
): Promise<AnnotatorQuality> {
  return api.get(`/people/${encodeURIComponent(username)}/quality/`);
}

export function setGoldStandard(
  volumeId: number,
  input: { is_gold_standard?: boolean; reference_submission?: number | null },
): Promise<{
  volume: number;
  is_gold_standard: boolean;
  reference_submission: number | null;
}> {
  return api.put(`/volumes/${volumeId}/gold-standard/`, input);
}

export interface ApprovedSubmission {
  id: number;
  task: number;
  annotator: string | null;
  source: string;
  submitted_at: string;
}

/**
 * Approved submissions on one volume — the candidates for a gold-standard
 * reference. Deliberately not `listSubmissions`, which returns the latest
 * *pending* row per task: that is the review queue, the opposite of what a
 * trusted reference is.
 */
export async function fetchApprovedSubmissions(
  volumeId: number,
): Promise<ApprovedSubmission[]> {
  const result = await api.get<{ results: ApprovedSubmission[] }>(
    `/volumes/${volumeId}/approved-submissions/`,
  );
  return result.results;
}
