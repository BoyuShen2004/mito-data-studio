import { api } from "./client";

export interface ProjectStatistics {
  project: { id: number; title: string; paused: boolean; priority: number };
  tasks: { total: number; approved: number; percent_complete: number };
  reviews: { total: number; rejection_rate: number | null };
  elapsed: {
    mean_elapsed_to_submit_seconds: number | null;
    mean_elapsed_to_approve_seconds: number | null;
    mean_elapsed_cycle_seconds: number | null;
  };
}

export const getProjectStatistics = (projectId: number) =>
  api.get<ProjectStatistics>(`/statistics/project/${projectId}/`);
