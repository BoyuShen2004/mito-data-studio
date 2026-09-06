import { useCallback, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { categoryLabel } from "../features/viewer/hardCaseCategory";
import { displayTaskLayerRange } from "../features/viewer/layerIndex";
import { setHardCaseStatus } from "../api/hardCases";
import { lifecycleLabel } from "../labels";
import { relativeTime } from "../time";
import type { AnnotationTask } from "../types/task";
import type { HardCase } from "../types/hardCase";
import type { Project } from "../types/project";
import HardCaseNotesModal from "./HardCaseNotesModal";

/**
 * The one list of work in this application.
 *
 * A task, a hard case and a project are the same shape — a number, a title, a
 * state, a most-recent event, a person — so they get one row renderer and one
 * filter bar rather than the four bespoke tables this replaces (`TaskTable`,
 * `HardCaseList`, and the inline tables on the project list and the manager
 * dashboard).
 *
 * Filtering is a `useMemo` over rows the caller already fetched: these lists
 * are tens of rows, every field the filters need is already on the wire, and a
 * round trip per dropdown change would cost more than it saves. Dropdown
 * options are the distinct values *present in `rows`*, so a filter can never
 * offer an option that yields nothing. The filter itself lives in the query
 * string (`useWorkFilter`), which is what makes "here is what is waiting on
 * you" a link instead of a description.
 */

export interface WorkFilter {
  /** "" (all) | "open" | "closed" | a concrete status value. */
  state?: string;
  /** Assignee for a task; whoever raised or registered it otherwise. */
  person?: string;
  /** Hard-case category, or project lifecycle. */
  category?: string;
  q?: string;
}

const KEYS = ["state", "person", "category", "q"] as const;

/** `WorkList`'s filter, in the query string. Writes merge into the existing
 * params so they never clobber `?tab=`, and `replace` so filing through
 * dropdowns does not fill the Back stack. */
export function useWorkFilter(): [WorkFilter, (next: WorkFilter) => void] {
  const [params, setParams] = useSearchParams();

  const filter = useMemo(() => {
    const out: WorkFilter = {};
    for (const key of KEYS) {
      const value = params.get(key);
      if (value) out[key] = value;
    }
    return out;
  }, [params]);

  const setFilter = useCallback(
    (next: WorkFilter) =>
      setParams((previous) => {
        const merged = new URLSearchParams(previous);
        for (const key of KEYS) {
          const value = next[key];
          if (value) merged.set(key, value);
          else merged.delete(key);
        }
        return merged;
      }, { replace: true }),
    [setParams],
  );

  return [filter, setFilter];
}

interface Chip {
  key: string;
  label: string;
  className?: string;
}

/** What every kind of row narrows to before it is rendered or filtered. */
interface WorkRow {
  key: string;
  id: number;
  /** The row this came from. Carried rather than looked back up by `id`: a
   * completed-task list holds both a task and its withdrawal record, which
   * share an id and differ only by `history_key`. */
  item: AnnotationTask | HardCase | Project;
  href: string;
  title: string;
  /** Concrete status value — the dot colour and the status dropdown. */
  state: string;
  stateLabel: string;
  open: boolean;
  subtitle: string;
  chips: Chip[];
  /** Assignee for a task, author otherwise; one column, one dropdown. */
  person: string;
  category: string;
  /** Settled rows (closed, withdrawn) fade rather than disappear. */
  dim: boolean;
  /** Lowercased haystack for the free-text box. */
  text: string;
}

const stateLabel = (value: string) => value.replace(/_/g, " ");
const haystack = (...parts: (string | undefined)[]) =>
  parts.filter(Boolean).join(" ").toLowerCase();

const DECISION_VERB: Record<string, string> = {
  approved: "Approved",
  rejected: "Rejected",
  revision_requested: "Revision requested",
};

/** The latest of the events a row already carries — never a fixed priority
 * order, because a resubmission is newer than the decision that asked for it. */
function latestEvent(events: [string | null | undefined, string][]): string {
  let best: [number, string] | null = null;
  for (const [at, text] of events) {
    const when = at ? new Date(at).getTime() : NaN;
    if (Number.isNaN(when)) continue;
    if (!best || when >= best[0]) best = [when, text];
  }
  return best ? best[1] : "";
}

function taskRow(task: AnnotationTask, showProject: boolean): WorkRow {
  const who = task.assigned_to_username;
  const withdrawn = Boolean(task.assignment_withdrawn);
  const subtitle = withdrawn
    ? [
        `${task.assignment_transferred ? "Transferred" : "Withdrawn"}${
          task.withdrawn_at ? ` ${relativeTime(task.withdrawn_at)}` : ""
        }`,
        task.withdrawal_reason,
      ].filter(Boolean).join(" · ")
    : latestEvent([
        [task.created_at, `Opened ${relativeTime(task.created_at)}`],
        [task.assigned_at, `Assigned ${relativeTime(task.assigned_at)}${who ? ` to ${who}` : ""}`],
        [task.submitted_at, `Submitted ${relativeTime(task.submitted_at)}${who ? ` by ${who}` : ""} · awaiting review`],
        [task.last_decision_at, `${DECISION_VERB[task.last_decision] ?? "Reviewed"} ${relativeTime(task.last_decision_at)}${
          task.last_decision_by_username ? ` by ${task.last_decision_by_username}` : ""
        }`],
      ]);

  return {
    key: task.history_key ?? String(task.id),
    id: task.id,
    item: task,
    href: `/tasks/${task.id}`,
    title: `${task.volume_name} z${displayTaskLayerRange(task.z_start, task.z_end)}`,
    state: task.status,
    stateLabel: stateLabel(task.status),
    open: task.status !== "approved",
    subtitle,
    chips: [
      ...(showProject && task.project_title ? [{ key: "project", label: task.project_title }] : []),
      ...(task.annotation_locked ? [{ key: "locked", label: "🔒 closed" }] : []),
    ],
    person: who,
    category: "",
    dim: withdrawn || task.status === "approved",
    text: haystack(task.volume_name, task.project_title, task.dataset, task.instructions),
  };
}

function caseRow(hardCase: HardCase, showProject: boolean): WorkRow {
  const range = displayTaskLayerRange(hardCase.z_start, hardCase.z_end);
  return {
    key: String(hardCase.id),
    id: hardCase.id,
    item: hardCase,
    href: hardCase.app_url,
    title: hardCase.note.split("\n")[0]?.trim() || `Label #${hardCase.label_id}`,
    state: hardCase.status,
    stateLabel: hardCase.status === "open" ? "open" : "taken down",
    open: hardCase.status === "open",
    subtitle: [
      showProject ? hardCase.project_title : "",
      `${hardCase.volume_name || "volume"} · z${range}`,
      `task #${hardCase.task}`,
      `raised ${relativeTime(hardCase.created_at)} by ${hardCase.created_by_username || "—"}`,
      hardCase.status === "resolved" && hardCase.resolved_by_username
        ? `taken down by ${hardCase.resolved_by_username}`
        : "",
    ].filter(Boolean).join(" · "),
    chips: [
      ...(hardCase.category
        ? [{
            key: "category",
            label: categoryLabel(hardCase.category),
            className: `hard-case-category-${hardCase.category}`,
          }]
        : []),
      ...(hardCase.revoked ? [{ key: "revoked", label: "public link revoked" }] : []),
    ],
    person: hardCase.created_by_username,
    // Blank stays a real value: an uncategorised case is filed as such, never
    // guessed into a category (docs/product-invariants.md).
    category: hardCase.category || "uncategorised",
    dim: hardCase.status === "resolved",
    text: haystack(hardCase.note, hardCase.volume_name, hardCase.project_title),
  };
}

const CLOSED_PROJECT_STATES = new Set(["completed", "delivered", "cancelled"]);

function projectRow(project: Project): WorkRow {
  const closed = CLOSED_PROJECT_STATES.has(project.status);
  return {
    key: String(project.id),
    id: project.id,
    item: project,
    href: `/projects/${project.id}`,
    title: project.title,
    state: project.status,
    stateLabel: stateLabel(project.status),
    open: !closed,
    subtitle: [
      `${project.volume_count} volume${project.volume_count === 1 ? "" : "s"}`,
      `${project.task_count} task${project.task_count === 1 ? "" : "s"}`,
      `deadline ${project.deadline ?? "—"}`,
      `created ${relativeTime(project.created_at)}${
        project.created_by_username ? ` by ${project.created_by_username}` : ""
      }`,
    ].join(" · "),
    chips: [
      { key: "lifecycle", label: lifecycleLabel(project.lifecycle) },
      ...(project.manager_reviewed
        ? []
        : [{ key: "approval", label: "awaiting approval", className: "work-chip-warn" }]),
    ],
    person: project.created_by_username,
    // Lifecycle rides the category slot, so the shared dropdown covers what
    // the requester dashboard's separate LifecycleTabs used to.
    category: project.lifecycle,
    dim: closed,
    text: haystack(project.title, project.description, project.institution_name),
  };
}

const matchesState = (row: WorkRow, value: string) =>
  value === ""
    ? true
    : value === "open"
      ? row.open
      : value === "closed"
        ? !row.open
        : row.state === value;

/** Distinct values present in `rows`, in first-seen order. */
function distinct(rows: WorkRow[], pick: (row: WorkRow) => string): string[] {
  const seen: string[] = [];
  for (const row of rows) {
    const value = pick(row);
    if (value && !seen.includes(value)) seen.push(value);
  }
  return seen;
}

type Props = {
  filter?: WorkFilter;
  onFilterChange?: (filter: WorkFilter) => void;
  emptyText?: ReactNode;
  loading?: boolean;
  /** Rows carry their project title; off inside one project. */
  showProject?: boolean;
  /** Re-fetch after a row action mutated something. */
  onChanged?: () => void;
  label?: string;
  /** Row checkboxes. The bulk bar below only appears once something is ticked
   * — a control that changes many rows must not sit there looking available
   * when it would change none. */
  selectable?: boolean;
  selected?: number[];
  onSelectionChange?: (ids: number[]) => void;
  bulkActions?: (ids: number[]) => ReactNode;
} & (
  | { kind: "task"; rows: AnnotationTask[] }
  | { kind: "case"; rows: HardCase[] }
  | { kind: "project"; rows: Project[] }
);

export default function WorkList(props: Props) {
  const {
    kind,
    filter = {},
    onFilterChange,
    emptyText = "Nothing here.",
    loading = false,
    showProject = true,
    onChanged,
    label = "Work list",
    selectable = false,
    selected = [],
    onSelectionChange,
    bulkActions,
  } = props;
  const { user, isManager } = useAuth();
  const [notesCase, setNotesCase] = useState<HardCase | null>(null);

  const rows = useMemo<WorkRow[]>(() => {
    if (props.kind === "task") return props.rows.map((row) => taskRow(row, showProject));
    if (props.kind === "case") return props.rows.map((row) => caseRow(row, showProject));
    return props.rows.map(projectRow);
  }, [props.kind, props.rows, showProject]);

  // Everything except the state filter, so the Open/Closed counts say how many
  // rows clicking them would actually show.
  const narrowed = useMemo(() => {
    const query = (filter.q ?? "").trim().toLowerCase();
    return rows.filter(
      (row) =>
        (!filter.person || row.person === filter.person) &&
        (!filter.category || row.category === filter.category) &&
        (!query || row.text.includes(query) || String(row.id) === query.replace(/^#/, "")),
    );
  }, [rows, filter.person, filter.category, filter.q]);

  const visible = useMemo(
    () => narrowed.filter((row) => matchesState(row, filter.state ?? "")),
    [narrowed, filter.state],
  );

  const openCount = narrowed.filter((row) => row.open).length;
  const closedCount = narrowed.length - openCount;
  const set = (patch: WorkFilter) => onFilterChange?.({ ...filter, ...patch });

  const states = distinct(rows, (row) => row.state);
  const people = distinct(rows, (row) => row.person);
  const categories = distinct(rows, (row) => row.category);

  const closedWord = kind === "case" ? "Taken down" : kind === "project" ? "Finished" : "Closed";
  const personLabel = kind === "task" ? "Assignee" : kind === "case" ? "Raised by" : "Registered by";
  const categoryLabelText = kind === "case" ? "Category" : "Lifecycle";

  const selectedSet = new Set(selected);
  const visibleIds = visible.map((row) => row.id);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedSet.has(id));
  const toggleRow = (id: number) =>
    onSelectionChange?.(
      selectedSet.has(id) ? selected.filter((other) => other !== id) : [...selected, id],
    );
  const toggleAllVisible = () =>
    onSelectionChange?.(
      allVisibleSelected
        ? selected.filter((id) => !visibleIds.includes(id))
        : [...selected.filter((id) => !visibleIds.includes(id)), ...visibleIds],
    );

  return (
    <>
      <div className="work-list">
        <div className="work-list-toolbar">
          {selectable && (
            <label className="work-select-all">
              <input
                type="checkbox"
                checked={allVisibleSelected}
                onChange={toggleAllVisible}
                aria-label="Select every row shown"
              />
            </label>
          )}
          <div className="work-list-counts" role="group" aria-label="Filter by open or closed">
            <button
              type="button"
              className={`work-count${filter.state === "open" ? " work-count-active" : ""}`}
              aria-pressed={filter.state === "open"}
              onClick={() => set({ state: filter.state === "open" ? "" : "open" })}
            >
              <span className="work-count-dot work-state-open" aria-hidden="true" />
              {openCount} Open
            </button>
            <button
              type="button"
              className={`work-count${filter.state === "closed" ? " work-count-active" : ""}`}
              aria-pressed={filter.state === "closed"}
              onClick={() => set({ state: filter.state === "closed" ? "" : "closed" })}
            >
              {closedCount} {closedWord}
            </button>
          </div>

          <div className="work-list-filters">
            <label className="work-filter">
              <span className="sr-only">Search</span>
              <input
                type="search"
                placeholder="Filter…"
                value={filter.q ?? ""}
                onChange={(event) => set({ q: event.target.value })}
              />
            </label>
            <FilterSelect
              label={personLabel}
              anyLabel={`${personLabel}: any`}
              value={filter.person}
              options={people}
              onChange={(person) => set({ person })}
            />
            {kind !== "task" && (
              <FilterSelect
                label={categoryLabelText}
                anyLabel={`${categoryLabelText}: any`}
                value={filter.category}
                options={categories}
                optionLabel={
                  kind === "project"
                    ? lifecycleLabel
                    : (value) => (value === "uncategorised" ? "Uncategorised" : categoryLabel(value))
                }
                onChange={(category) => set({ category })}
              />
            )}
            <FilterSelect
              label="State"
              anyLabel="State: any"
              value={filter.state}
              options={states}
              optionLabel={stateLabel}
              onChange={(state) => set({ state })}
              alwaysShown
            >
              <option value="open">Open</option>
              <option value="closed">{closedWord}</option>
            </FilterSelect>
          </div>
        </div>

        {selectable && selected.length > 0 && (
          <div className="work-bulk-bar">
            <span>{selected.length} selected</span>
            <div className="row">
              {bulkActions?.(selected)}
              <button type="button" className="secondary" onClick={() => onSelectionChange?.([])}>
                Clear
              </button>
            </div>
          </div>
        )}

        {loading ? (
          <p className="muted work-list-empty">Loading…</p>
        ) : visible.length === 0 ? (
          <div className="work-list-empty muted">
            {rows.length === 0 ? emptyText : "No rows match this filter."}
          </div>
        ) : (
          <ul className="work-rows" aria-label={label}>
            {visible.map((row) => (
              <li key={row.key} className={`work-row${row.dim ? " work-row-dim" : ""}`}>
                {selectable && (
                  <input
                    type="checkbox"
                    className="work-row-select"
                    checked={selectedSet.has(row.id)}
                    onChange={() => toggleRow(row.id)}
                    aria-label={`Select ${row.title}`}
                  />
                )}
                <span
                  className={`work-state-dot work-state-${row.state}`}
                  role="img"
                  aria-label={`State: ${row.stateLabel}`}
                  title={row.stateLabel}
                />
                <div className="work-row-body">
                  <div className="work-row-title-line">
                    <Link to={row.href} className="work-row-title">{row.title}</Link>
                    {row.chips.map((chip) => (
                      <span key={chip.key} className={`work-chip${chip.className ? ` ${chip.className}` : ""}`}>
                        {chip.label}
                      </span>
                    ))}
                  </div>
                  <div className="work-row-subtitle muted" title={row.subtitle}>{row.subtitle}</div>
                </div>
                <div className="work-row-side">
                  <Link to={row.href} className="work-row-number">#{row.id}</Link>
                  {row.person && (
                    <span className="work-row-person" title={`${personLabel}: ${row.person}`}>
                      {row.person}
                    </span>
                  )}
                </div>
                <div className="work-row-actions">
                  {kind === "task" && (
                    <TaskRowActions
                      task={row.item as AnnotationTask}
                      isManager={isManager}
                      userId={user?.id}
                    />
                  )}
                  {kind === "case" && (
                    <CaseRowActions
                      hardCase={row.item as HardCase}
                      onNotes={setNotesCase}
                      onChanged={onChanged}
                    />
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
      {notesCase && (
        <HardCaseNotesModal
          hardCase={notesCase}
          onClose={() => setNotesCase(null)}
          onChanged={onChanged}
        />
      )}
    </>
  );
}

/** One dropdown over the distinct values present in the loaded rows, so a
 * filter can never offer an option that yields nothing — and so a field with
 * nothing to choose between renders no control at all. */
function FilterSelect({
  label,
  anyLabel,
  value,
  options,
  optionLabel = (option: string) => option,
  onChange,
  alwaysShown = false,
  children,
}: {
  label: string;
  anyLabel: string;
  value: string | undefined;
  options: string[];
  optionLabel?: (option: string) => string;
  onChange: (value: string) => void;
  /** State is offered even with one status present: Open/Closed still apply. */
  alwaysShown?: boolean;
  children?: ReactNode;
}) {
  if (options.length === 0 && !alwaysShown) return null;
  return (
    <label className="work-filter">
      <span className="sr-only">{label}</span>
      <select value={value ?? ""} onChange={(event) => onChange(event.target.value)}>
        <option value="">{anyLabel}</option>
        {children}
        {options.map((option) => (
          <option key={option} value={option}>{optionLabel(option)}</option>
        ))}
      </select>
    </label>
  );
}

/** View / Annotate stay on the row: they are this application's primary verbs
 * and a list that hides them behind a hop is a list nobody works from. */
function TaskRowActions({
  task,
  isManager,
  userId,
}: {
  task: AnnotationTask;
  isManager: boolean;
  userId: number | undefined;
}) {
  if (task.assignment_withdrawn) {
    return <span className="muted">{task.assignment_transferred ? "Transferred" : "Withdrawn"}</span>;
  }
  const canEdit = (isManager || task.assigned_to === userId) && task.can_annotate;
  return (
    <>
      <Link to={`/viewer/tasks/${task.id}`}>
        <button type="button" className="secondary">View</button>
      </Link>
      {canEdit && (
        <Link to={`/editor/tasks/${task.id}`}>
          <button type="button">Annotate</button>
        </Link>
      )}
    </>
  );
}

function CaseRowActions({
  hardCase,
  onNotes,
  onChanged,
}: {
  hardCase: HardCase;
  onNotes: (hardCase: HardCase) => void;
  onChanged?: () => void;
}) {
  const takeDown = async () => {
    const next = hardCase.status === "open" ? "resolved" : "open";
    if (
      next === "resolved" &&
      !window.confirm(
        `Take down hard case #${hardCase.id}? It stays readable for everyone on the project, but drops off the open list.`,
      )
    ) {
      return;
    }
    await setHardCaseStatus(hardCase.id, next);
    onChanged?.();
  };

  return (
    <>
      <button type="button" className="secondary" onClick={() => onNotes(hardCase)}>
        Note{hardCase.message_count ? ` (${hardCase.message_count})` : ""}
      </button>
      {hardCase.can_take_down && (
        <button type="button" className="secondary" onClick={() => void takeDown()}>
          {hardCase.status === "open" ? "Take down" : "Reopen"}
        </button>
      )}
    </>
  );
}
