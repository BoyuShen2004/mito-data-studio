import { api } from "./client";

/**
 * Automatic annotation time tracking.
 *
 * The client's only job is to say "I am still here" on the cadence the server
 * hands it. It never proposes a duration, a start time or an end time — every
 * credited second comes from the server clock, and a request carrying a
 * duration would simply be ignored.
 */

/** Cumulative time for one task or volume. */
export interface AnnotationTimeSummary {
  /** False means legacy-exempt: render `-`, never `0m`. */
  tracked: boolean;
  /** `null` exactly when `tracked` is false. */
  seconds: number | null;
  /** Server-formatted compact duration, already `-` for legacy. */
  display: string;
  eligibility?: string;
}

/** Protocol constants, served rather than hardcoded so the two sides agree. */
export interface TimingConfig {
  heartbeat_seconds: number;
  hidden_grace_seconds: number;
  idle_seconds: number;
  abandon_grace_seconds: number;
  max_interval_seconds: number;
  server_idle_timeout_seconds: number;
}

/** The single shape every timing endpoint answers with. */
export interface TimingStatus {
  task_id: number;
  volume_id: number;
  /** Is a session open and accruing right now? */
  tracking: boolean;
  /** Is this volume measured at all? */
  eligible: boolean;
  /** `ok`, `not_assigned`, `legacy_exempt`, `not_editable`, `closed`, `gone`. */
  reason: string;
  session_id: string | null;
  total_seconds: number | null;
  display: string;
  config: TimingConfig;
}

export const getTaskTiming = (taskId: number, sessionId?: string | null) =>
  api.get<TimingStatus>(
    `/tasks/${taskId}/timing/${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`,
  );

export const startTaskTiming = (taskId: number, clientToken: string) =>
  api.post<TimingStatus>(`/tasks/${taskId}/timing/start/`, {
    client_token: clientToken,
  });

export const heartbeatTaskTiming = (taskId: number, sessionId: string) =>
  api.post<TimingStatus>(`/tasks/${taskId}/timing/heartbeat/`, {
    session_id: sessionId,
  });

export const stopTaskTiming = (
  taskId: number,
  sessionId: string,
  reason = "ended",
) =>
  api.post<TimingStatus>(`/tasks/${taskId}/timing/stop/`, {
    session_id: sessionId,
    reason,
  });

// --- Manager drill-down reporting ------------------------------------------

export interface VolumeTimeRow {
  volume_id: number;
  volume_name: string;
  /** False = legacy-exempt; `seconds` is null and `display` is `-`. */
  tracked: boolean;
  seconds: number | null;
  display: string;
}

export interface DatasetTimeRow {
  dataset_id: number | null;
  dataset_name: string;
  seconds: number;
  display: string;
  /** Volumes underneath whose time is unknowable. */
  legacy_volumes: number;
  /** True when `seconds` is real but incomplete — the UI must say so. */
  has_legacy: boolean;
  volumes: VolumeTimeRow[];
}

export interface ProjectTimeRow {
  project_id: number;
  project_title: string;
  seconds: number;
  display: string;
  legacy_volumes: number;
  has_legacy: boolean;
  datasets: DatasetTimeRow[];
}

export interface AnnotatorTimeReport {
  annotator: string;
  seconds: number;
  display: string;
  legacy_volumes: number;
  has_legacy: boolean;
  projects: ProjectTimeRow[];
}

/** Project → dataset → volume, already folded, in one request.
 *  Managers may read anyone's; everyone may read their own. */
export const getAnnotatorTime = (username: string) =>
  api.get<AnnotatorTimeReport>(
    `/people/${encodeURIComponent(username)}/time/`,
  );
