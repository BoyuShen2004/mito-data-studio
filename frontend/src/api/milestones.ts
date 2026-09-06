import { api } from "./client";
import type {
  BurndownPoint,
  Milestone,
  MilestoneProgress,
  ProjectDelivery,
} from "../types/milestone";

export function fetchMilestones(
  projectId: number,
): Promise<{ results: Milestone[]; can_edit: boolean }> {
  return api.get(`/projects/${projectId}/milestones/`);
}

export interface MilestoneInput {
  name: string;
  description?: string;
  due_on: string;
  target_metric?: "tasks_approved" | "volumes_completed";
  target_value: number;
  volumes?: number[];
  status?: string;
  order?: number;
}

export function createMilestone(
  projectId: number,
  input: MilestoneInput,
): Promise<Milestone> {
  return api.post(`/projects/${projectId}/milestones/`, input);
}

export function updateMilestone(
  milestoneId: number,
  input: Partial<MilestoneInput>,
): Promise<Milestone> {
  return api.patch(`/milestones/${milestoneId}/`, input);
}

export function deleteMilestone(milestoneId: number): Promise<void> {
  return api.del(`/milestones/${milestoneId}/`);
}

export function fetchProjectDelivery(
  projectId: number,
  days = 30,
): Promise<ProjectDelivery> {
  return api.get(`/projects/${projectId}/delivery/?days=${days}`);
}

export interface MilestoneDetail extends Milestone {
  burndown: { actual: BurndownPoint[]; ideal: BurndownPoint[] };
  progress: MilestoneProgress;
}

/** One milestone with its burndown series. Fetched only when a card is opened. */
export function fetchMilestoneDetail(
  milestoneId: number,
): Promise<MilestoneDetail> {
  return api.get(`/milestones/${milestoneId}/`);
}

/**
 * The same three panels across every project. Manager-only server-side.
 *
 * Separate from `fetchProjectDelivery` rather than an optional argument: the
 * two have different permissions, and a call site should have to say which one
 * it means.
 */
export function fetchDeliveryOverview(days = 30): Promise<ProjectDelivery> {
  return api.get(`/delivery/?days=${days}`);
}
