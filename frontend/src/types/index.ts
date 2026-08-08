// Shared enums / literal unions mirroring the Django `TextChoices`.

export type Role =
  | "manager"
  | "annotator"
  | "requester"
  | "client"
  | "reviewer"
  | null;

export type LabelType = "none" | "prediction" | "partial";

export type TaskType =
  | "manual_annotation"
  | "prediction_proofreading"
  | "final_review"
  | "qc_review";

export type TaskStatus =
  | "unassigned"
  | "assigned"
  | "in_progress"
  | "submitted"
  | "approved"
  | "rejected"
  | "revision_requested"
  | "cancelled";

export type AnnotationType =
  | "semantic_segmentation"
  | "instance_segmentation"
  | "proofreading";

export type ProjectStatus =
  | "draft"
  | "active"
  | "in_annotation"
  | "in_review"
  | "completed"
  | "delivered"
  | "cancelled";

export type QCStatus = "not_run" | "passed" | "warning" | "failed";

export type ReviewDecision = "approved" | "rejected" | "revision_requested";

export interface CurrentUser {
  id: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  is_superuser: boolean;
  role: Role;
  institution_name: string;
  /** Short self-editable profile (People page); blank until they fill it in. */
  display_name: string;
  contact_note: string;
  /** Per-user annotate tool shortcuts, `{tool: letter}`. Always complete (the
   * server fills unset tools from the defaults), so the editor can bind
   * straight from it. The modifier is *not* here: it is Cmd on macOS and Ctrl
   * elsewhere, decided by the machine, not by the account. */
  annotate_shortcuts: Record<string, string>;
  /** What Reset to defaults restores. */
  annotate_shortcut_defaults: Record<string, string>;
  /** The bindable tools, in tool-strip order, with their UI labels. */
  annotate_shortcut_tools: { tool: string; label: string }[];
  /** False for requesters, who have no annotation tools. */
  can_customize_shortcuts: boolean;
}
