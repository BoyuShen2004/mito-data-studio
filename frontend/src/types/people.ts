// People — who works with whom. One card shape for every role (the backend
// projects the same project-membership relation into role-specific lists), so
// the UI renders panels by "is this list non-empty", not by branching on role.
// See backend/accounts/services.py.

export interface PersonProjectBrief {
  id: number;
  title: string;
  dataset: string;
  status: string;
  manager_reviewed: boolean;
  deadline: string | null;
  /** Requester panel only: usernames of the manager(s) running the project. */
  managers?: string[];
  task_count?: number;
}

/** Free-form because the numbers differ per role (an annotator has task
 * counts, a requester has project counts); every value is a plain number
 * except the two `last_decision*` fields the manager panel shows. */
export interface PersonStats {
  [key: string]: number | string | null;
}

export interface Person {
  id: number;
  username: string;
  display_name: string;
  role: string;
  institution_name: string;
  contact_note: string;
  email: string;
  projects?: PersonProjectBrief[];
  stats?: PersonStats;
}

export interface PeopleOverview {
  me: Person;
  role: string;
  /** Annotator/requester: the manager(s) on their projects. */
  managers: Person[];
  /** Annotator: peer annotators on shared projects. Requester: who is working. */
  peers: Person[];
  /** Manager: the annotator roster with workload + review record. */
  annotators: Person[];
  /** Manager: the customers, with the projects they registered. */
  requesters: Person[];
  projects: PersonProjectBrief[];
}

export interface ProfileUpdate {
  display_name?: string;
  institution_name?: string;
  contact_note?: string;
  /** `{tool: letter}`; the server validates conflicts and rejects the whole
   * map rather than storing a binding that cannot fire. */
  annotate_shortcuts?: Record<string, string>;
}
