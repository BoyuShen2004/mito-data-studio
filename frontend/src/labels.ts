// Centralised display labels (internal value -> user-facing text).
//
// Donglai's design calls the data-owning role an "Institution"; the backend
// keeps the stable internal role value `requester`. This module is the single
// place that maps internal identifiers to the words shown in the UI, so React
// components never hard-code "Institution" / "Requester" inline.
//
// Keep in sync with the backend mirror in `backend/core/labels.py`.

import type { AnnotationType, ProjectStatus, Role } from "./types";

export type Lifecycle = "new" | "to_proofread" | "done";
export type WorkflowType = "annotation" | "proofreading" | "segmentation";

export const ROLE_LABELS: Record<string, string> = {
  manager: "Manager",
  annotator: "Annotator",
  requester: "Institution",
  client: "Institution",
  reviewer: "Reviewer",
};

export const LIFECYCLE_LABELS: Record<Lifecycle, string> = {
  new: "New",
  to_proofread: "To Proofread",
  done: "Done",
};

// Ordered for navigation / tabs.
export const LIFECYCLE_ORDER: Lifecycle[] = ["new", "to_proofread", "done"];

export function titleize(value: string): string {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function roleLabel(role: Role | string | null | undefined): string {
  if (!role) return "";
  return ROLE_LABELS[role] ?? titleize(role);
}

export function lifecycleLabel(value: Lifecycle | string): string {
  return LIFECYCLE_LABELS[value as Lifecycle] ?? titleize(value);
}

// --- Project option lists -------------------------------------------------
//
// The exact values Django accepts (core.choices.AnnotationType /
// ProjectStatus). Forms must offer these and nothing else: a value outside
// them is rejected by the API with a 400.

export const ANNOTATION_TYPES: { value: AnnotationType; label: string }[] = [
  { value: "instance_segmentation", label: "Instance segmentation" },
  { value: "semantic_segmentation", label: "Semantic segmentation" },
  { value: "proofreading", label: "Proofreading" },
];

export const PROJECT_STATUSES: ProjectStatus[] = [
  "draft",
  "active",
  "in_annotation",
  "in_review",
  "completed",
  "delivered",
  "cancelled",
];

// --- Task priority & difficulty -------------------------------------------
//
// Stored as integers 1–5 (see core.choices.PriorityLevel / DifficultyLevel) but
// shown as words, since a bare number gives no hint which end is which.

export interface Level {
  value: number;
  label: string;
}

export const PRIORITY_LEVELS: Level[] = [
  { value: 1, label: "Lowest" },
  { value: 2, label: "Low" },
  { value: 3, label: "Normal" },
  { value: 4, label: "High" },
  { value: 5, label: "Urgent" },
];

export const DIFFICULTY_LEVELS: Level[] = [
  { value: 1, label: "Very easy" },
  { value: 2, label: "Easy" },
  { value: 3, label: "Moderate" },
  { value: 4, label: "Hard" },
  { value: 5, label: "Very hard" },
];

function levelLabel(levels: Level[], value: number | null | undefined): string {
  const match = levels.find((l) => l.value === value);
  return match ? match.label : value != null ? `Level ${value}` : "—";
}

export const priorityLabel = (v: number | null | undefined) =>
  levelLabel(PRIORITY_LEVELS, v);
export const difficultyLabel = (v: number | null | undefined) =>
  levelLabel(DIFFICULTY_LEVELS, v);
