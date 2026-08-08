import type { DatasetMetadata, Project, ProjectSummary } from "../types/project";
import { api } from "./client";

export interface ProjectInput {
  title: string;
  dataset?: string;
  description?: string;
  metadata?: DatasetMetadata;
  annotation_type?: string;
  annotation_target?: string;
  status?: string;
  deadline?: string | null;
  priority?: number;
  paused?: boolean;
  teams?: number[];
}

export const listProjects = (lifecycle?: string) =>
  api.get<Project[]>(
    lifecycle ? `/projects/?lifecycle=${encodeURIComponent(lifecycle)}` : "/projects/",
  );

export const getProject = (id: number) => api.get<Project>(`/projects/${id}/`);

export const createProject = (data: ProjectInput) =>
  api.post<Project>("/projects/", data);

export const updateProject = (id: number, data: Partial<ProjectInput>) =>
  api.patch<Project>(`/projects/${id}/`, data);

export const deleteProject = (id: number) => api.del<void>(`/projects/${id}/`);

export const getProjectSummary = (id: number) =>
  api.get<ProjectSummary>(`/projects/${id}/summary/`);

// Manager marks a project reviewed (or not), enabling/disabling assignment.
export const reviewProject = (id: number, reviewed = true) =>
  api.post<Project>(`/projects/${id}/review/`, { reviewed });

export interface ProjectMember {
  user_id: number;
  username: string;
  display_name: string;
  is_explicit: boolean;
  is_working_team: boolean;
  has_tasks: boolean;
  access_reason: "Project member" | "Working team" | "Via assigned task";
  membership_id: number | null;
  created_at: string | null;
}

export const listProjectMembers = (id: number) =>
  api.get<ProjectMember[]>(`/projects/${id}/members/`);

export const addProjectMember = (id: number, userId: number) =>
  api.post(`/projects/${id}/members/`, { user_id: userId });

export const removeProjectMember = (id: number, userId: number) =>
  api.del<void>(`/projects/${id}/members/${userId}/`);
