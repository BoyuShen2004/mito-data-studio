import { api } from "./client";

export interface CollaborationAnnotator {
  id: number;
  username: string;
  role: string;
}

export interface TeamMember {
  user_id: number;
  username: string;
  role?: string;
}

export interface CollaborationTeam {
  id: number;
  name: string;
  description?: string;
  organization_id?: number;
  organization_name?: string;
  members: TeamMember[];
  delete_impact?: {
    project_count: number;
    task_count: number;
    projects: Array<{id: number; title: string; task_count: number}>;
  };
}

export interface CollaborationState {
  institutions: Array<{id: number; name: string}>;
  users: CollaborationAnnotator[];
  teams: CollaborationTeam[];
  mutation?: {
    action: string;
    team_id?: number | null;
    project_id?: number;
    withdrawn?: number;
    [key: string]: unknown;
  };
}

export const getCollaboration = () => api.get<CollaborationState>("/collaboration/");
export const mutateCollaboration = (body: Record<string, unknown>) =>
  api.post<CollaborationState>("/collaboration/", body);
