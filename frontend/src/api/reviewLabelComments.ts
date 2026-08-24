import type { ReviewLabelComment } from "../types/reviewLabelComment";
import type { ViewLocation } from "../features/viewer/viewLocation";
import { api } from "./client";

export const listReviewLabelComments = (submissionId?: number) =>
  api.get<ReviewLabelComment[]>(
    `/review-label-comments/${submissionId ? `?submission=${submissionId}` : ""}`,
  );

export const saveReviewLabelComment = (
  submission: number,
  task: number,
  labelId: number,
  body: string,
  location?: ViewLocation | null,
) =>
  api.post<ReviewLabelComment>("/review-label-comments/", {
    submission,
    task,
    label_id: labelId,
    body,
    ...(location
      ? {
          view_z: location.z,
          view_y: location.y,
          view_x: location.x,
          view_axis: location.axis,
        }
      : {}),
  });

export const deleteReviewLabelComment = (commentId: number) =>
  api.del<void>(`/review-label-comments/${commentId}/`);
