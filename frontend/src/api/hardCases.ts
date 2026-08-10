import type { HardCase, HardCaseMessage, HardCaseStatus } from "../types/hardCase";
import { api } from "./client";

/** Record the Active label on `taskId` as a hard case for its project.
 * Returns the full row — including `app_url` (in-app, for project members)
 * and `url`/`token` (the optional public copyable link). */
export const createHardCase = (taskId: number, labelId: number, note = "") =>
  api.post<HardCase>(`/tasks/${taskId}/hard-cases/`, { label_id: labelId, note });

/** The Hard Cases inbox, newest first. Server-scoped to what the caller may
 * see; `project`/`volume` narrow it for the per-project section. */
export const listHardCases = (params?: {
  project?: number;
  volume?: number;
  status?: HardCaseStatus;
}) => {
  const q = new URLSearchParams();
  if (params?.project != null) q.set("project", String(params.project));
  if (params?.volume != null) q.set("volume", String(params.volume));
  if (params?.status) q.set("status", params.status);
  const qs = q.toString();
  return api.get<HardCase[]>(`/hard-cases/${qs ? `?${qs}` : ""}`);
};

export const getHardCase = (id: number) => api.get<HardCase>(`/hard-cases/${id}/`);

/** Take a case down (`resolved`) or put it back on the board (`open`).
 * Creator or manager only; never deletes — members keep read access. */
export const setHardCaseStatus = (id: number, status: HardCaseStatus) =>
  api.post<HardCase>(`/hard-cases/${id}/status/`, { status });

/** Kill (or restore) the public token link only. */
export const setHardCaseRevoked = (id: number, revoked: boolean) =>
  api.post<HardCase>(`/hard-cases/${id}/revoke/`, { revoked });

export const updateHardCaseNote = (id: number, note: string) =>
  api.patch<HardCase>(`/hard-cases/${id}/note/`, { note });

export const listHardCaseMessages = (id: number) =>
  api.get<HardCaseMessage[]>(`/hard-cases/${id}/messages/`);

export const addHardCaseMessage = (id: number, body: string) =>
  api.post<HardCaseMessage>(`/hard-cases/${id}/messages/`, { body });
