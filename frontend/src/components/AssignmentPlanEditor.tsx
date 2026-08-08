import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  getCollaboration,
  mutateCollaboration,
  type CollaborationState,
} from "../api/collaboration";
import { applyAssignPlan, listPlanRows, previewAssignPlan, setTaskAnnotationLock } from "../api/tasks";
import { resetWorkingLabels } from "../api/viewer";
import { useAsync } from "../hooks/useAsync";
import { DIFFICULTY_LEVELS, PRIORITY_LEVELS, type Level } from "../labels";
import type {
  AssignmentPlanTask,
  PlanEntryInput,
  PlanEntryTask,
} from "../types/task";
import StatusBadge from "./StatusBadge";
import RegionCoverage from "./RegionCoverage";
import TeamEditor from "./teams/TeamEditor";
import { formatShape, formatVoxelSize } from "./VolumeMeta";

// A <select> over 1–5 levels. Falls back to showing an unexpected stored value
// so an out-of-range number is never silently changed just by opening the row.
function LevelSelect({
  levels,
  value,
  disabled,
  onChange,
}: {
  levels: Level[];
  value: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  const known = levels.some((l) => String(l.value) === value);
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      {!known && value !== "" && <option value={value}>Level {value}</option>}
      {levels.map((l) => (
        <option key={l.value} value={String(l.value)}>
          {l.label}
        </option>
      ))}
    </select>
  );
}

function InstructionsTextarea({
  value,
  disabled,
  onChange,
}: {
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  useLayoutEffect(() => {
    const textarea = ref.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;
  }, [value]);
  return (
    <textarea
      ref={ref}
      rows={2}
      placeholder="Notes for annotator"
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

// The manager-editable fields of a single planned task row. Kept as strings for
// the number/date inputs so partially-typed values don't fight the controls.
interface DraftRow {
  annotator_id: number | null;
  priority: string;
  difficulty: string;
  deadline: string; // "" == no deadline
  instructions: string;
}

// A task's own deadline wins; an unset one defaults to the project's overall
// deadline (still just a suggestion — nothing is written until the manager
// actually saves, so it stays live-synced to the project's deadline for any
// row no one has touched yet).
function toDraft(task: AssignmentPlanTask, projectDeadline: string | null): DraftRow {
  return {
    annotator_id: task.assigned_to,
    priority: String(task.priority),
    difficulty: String(task.difficulty),
    deadline: task.deadline ?? projectDeadline ?? "",
    instructions: task.instructions ?? "",
  };
}

function rowsEqual(a: DraftRow, b: DraftRow): boolean {
  return (
    a.annotator_id === b.annotator_id &&
    a.priority === b.priority &&
    a.difficulty === b.difficulty &&
    a.deadline === b.deadline &&
    a.instructions === b.instructions
  );
}

// Turn a dirty draft row into the payload the apply endpoint expects.
function toInput(task: AssignmentPlanTask, row: DraftRow): PlanEntryInput {
  return {
    task_id: task.id,
    annotator_id: row.annotator_id,
    priority: Number(row.priority) || 0,
    difficulty: Number(row.difficulty) || 0,
    deadline: row.deadline || null,
    instructions: row.instructions,
  };
}

// Manager-only editor for a project's whole assignment plan. Managers can
// auto-fill a balanced plan, hand-edit each task's annotator, priority,
// difficulty, deadline and instructions, then save the whole plan at once.
export default function AssignmentPlanEditor({
  projectId,
  projectTitle,
  workingTeamId,
  projectDeadline = null,
  onSaved,
}: {
  projectId: number;
  projectTitle: string;
  workingTeamId: number | null;
  projectDeadline?: string | null;
  onSaved?: () => void;
}) {
  // Ensures a task exists for every volume (creating any missing ones) and
  // lists them — no annotators proposed here, so the manager sees every
  // volume that needs a plan without first clicking "Auto-fill balanced
  // plan". That button (below) only ever proposes/fills values into rows
  // this already produced.
  const rows = useAsync(() => listPlanRows(projectId), [projectId]);
  const collaboration = useAsync(getCollaboration, []);

  // `original` is the last server-known state; `draft` is what the manager is
  // editing. Both are keyed by task id.
  const [original, setOriginal] = useState<Record<number, DraftRow>>({});
  const [draft, setDraft] = useState<Record<number, DraftRow>>({});
  const [order, setOrder] = useState<number[]>([]);
  const [meta, setMeta] = useState<Record<number, AssignmentPlanTask>>({});
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());

  const [busy, setBusy] = useState(false);
  const [creatingTeam, setCreatingTeam] = useState(false);
  const [selectedTeamId, setSelectedTeamId] = useState<number | null>(workingTeamId);
  const [collaborationState, setCollaborationState] = useState<CollaborationState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (collaboration.data) setCollaborationState(collaboration.data);
  }, [collaboration.data]);
  useEffect(() => setSelectedTeamId(workingTeamId), [workingTeamId]);

  // Load server rows into the draft the first time they arrive — every
  // volume already has a row here (see `listPlanRows`), so this is enough to
  // let the manager start editing without touching "Auto-fill" at all.
  const [rowsLoaded, setRowsLoaded] = useState(false);
  if (!rowsLoaded && rows.data) {
    const orig: Record<number, DraftRow> = {};
    const metaMap: Record<number, AssignmentPlanTask> = {};
    for (const t of rows.data.entries) {
      orig[t.id] = toDraft(t, projectDeadline);
      metaMap[t.id] = t;
    }
    setOriginal(orig);
    setDraft(orig);
    setMeta(metaMap);
    setOrder(rows.data.entries.map((t) => t.id));
    setRowsLoaded(true);
  }

  const dirtyIds = useMemo(
    () => order.filter((id) => original[id] && !rowsEqual(draft[id], original[id])),
    [order, draft, original],
  );

  const patch = (id: number, changes: Partial<DraftRow>) => {
    setDraft((d) => ({ ...d, [id]: { ...d[id], ...changes } }));
  };

  // Pull a fresh balanced plan and merge the proposed annotators into the draft
  // for tasks the manager hasn't already given someone.
  const autoFill = async () => {
    setBusy(true);
    setError(null);
    try {
      const plan = await previewAssignPlan(projectId);
      const nextDraft: Record<number, DraftRow> = {};
      const nextOrig: Record<number, DraftRow> = {};
      const metaMap: Record<number, AssignmentPlanTask> = {};
      const ids: number[] = [];
      for (const t of plan.entries) {
        const base = toDraft(t, projectDeadline);
        nextOrig[t.id] = base;
        metaMap[t.id] = t;
        ids.push(t.id);
        // Preserve any edits already in the current draft; otherwise adopt the
        // proposed annotator for unassigned tasks.
        const existing = draft[t.id];
        nextDraft[t.id] =
          existing && original[t.id] && !rowsEqual(existing, original[t.id])
            ? existing
            : { ...base, annotator_id: t.proposed_annotator_id };
      }
      setOriginal(nextOrig);
      setDraft(nextDraft);
      setMeta(metaMap);
      setOrder(ids);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not build a plan.");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (dirtyIds.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const entries = dirtyIds.map((id) => toInput(meta[id], draft[id]));
      await applyAssignPlan(projectId, entries);
      // Reload from the server so statuses/timestamps reflect the commit.
      const fresh = (await listPlanRows(projectId)).entries as PlanEntryTask[];
      const orig: Record<number, DraftRow> = {};
      const metaMap: Record<number, AssignmentPlanTask> = {};
      for (const t of fresh) {
        orig[t.id] = toDraft(t, projectDeadline);
        metaMap[t.id] = t;
      }
      setOriginal(orig);
      setDraft(orig);
      setMeta(metaMap);
      setOrder(fresh.map((t) => t.id));
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Saving the plan failed.");
    } finally {
      setBusy(false);
    }
  };

  const discard = () => {
    setDraft(original);
    setError(null);
  };

  if (rows.loading && !rowsLoaded) return <p className="muted">Loading tasks…</p>;
  if (rows.error && !rowsLoaded) return <div className="error">Could not load push assignments. {rows.error}<div><button type="button" className="secondary" onClick={rows.reload}>Retry</button></div></div>;

  const teamState = collaborationState ?? collaboration.data;
  const teams = teamState?.teams ?? [];
  const workingTeam = teams.find((team) => team.id === selectedTeamId);
  const annotators = (teamState?.users ?? []).filter(
    (user) => user.role === "annotator",
  );
  const workingMemberIds = new Set(
    (workingTeam?.members ?? []).map((member) => member.user_id),
  );

  const changeWorkingTeam = async (teamId: number | null) => {
    if (teamId === selectedTeamId) return;
    if (selectedTeamId && !window.confirm(
      "Change this project's working team? Assignments are kept for shared members; other assignments are withdrawn as cancelled.",
    )) return;
    setBusy(true);
    setError(null);
    try {
      const state = await mutateCollaboration({
        action: "set_project_working_team",
        project_id: projectId,
        team_id: teamId,
      });
      setCollaborationState(state);
      setSelectedTeamId(teamId);
      const nextTeam = state.teams.find((team) => team.id === teamId);
      const nextMemberIds = new Set((nextTeam?.members ?? []).map((member) => member.user_id));
      setDraft((current) => Object.fromEntries(
        Object.entries(current).map(([id, row]) => [id, {
          ...row,
          annotator_id: row.annotator_id != null && nextMemberIds.has(row.annotator_id)
            ? row.annotator_id
            : null,
        }]),
      ));
      setOriginal((current) => Object.fromEntries(
        Object.entries(current).map(([id, row]) => [id, {
          ...row,
          annotator_id: row.annotator_id != null && nextMemberIds.has(row.annotator_id)
            ? row.annotator_id
            : null,
        }]),
      ));
      onSaved?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update the working team.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {/* Toolbar is right-aligned alone so button label changes (Save plan /
          Save plan (N), Working…) never shove neighbors. Status text sits in a
          reserved-height slot so autofill/save notices never move the table. */}
      <div className="plan-toolbar">
        <button
          className="secondary plan-btn-autofill"
          onClick={autoFill}
          disabled={busy || !workingTeam}
        >
          {busy ? "Working…" : "Preview eligible auto-fill"}
        </button>
        <button
          className="secondary"
          onClick={discard}
          disabled={busy || dirtyIds.length === 0}
        >
          Discard changes
        </button>
        <button
          className="plan-btn-save"
          onClick={save}
          disabled={busy || dirtyIds.length === 0}
        >
          {busy
            ? "Saving…"
            : dirtyIds.length > 0
              ? `Save plan (${dirtyIds.length})`
              : "Save plan"}
        </button>
      </div>

      <div className="plan-status" aria-live="polite">
        {error ? <div className="error">{error}</div> : null}
      </div>

      <div className="working-team-panel">
        <div>
          <label className="field working-team-field">
            <span>Working team</span>
            <select
              aria-label="Working team"
              value={selectedTeamId ?? ""}
              disabled={busy || collaboration.loading}
              onChange={(event) => void changeWorkingTeam(event.target.value ? Number(event.target.value) : null)}
            >
              <option value="">Choose a team…</option>
              {teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}
            </select>
          </label>
          <p className="muted">
            {workingTeam
              ? `This project's work goes to members of ${workingTeam.name}.`
              : "Choose one team before assigning volumes."}
          </p>
        </div>
        <div className="row working-team-actions">
          <button type="button" className="secondary" onClick={() => setCreatingTeam((value) => !value)}>
            {creatingTeam ? "Close team creator" : "Create team"}
          </button>
          <a href="/people">Edit team in People →</a>
        </div>
        {creatingTeam && (
          <div className="card working-team-create">
            <TeamEditor
              annotators={annotators}
              teams={teams}
              defaultName={projectTitle}
              projectId={projectId}
              onChanged={setCollaborationState}
              onCreated={(team, state) => {
                setCollaborationState(state);
                setSelectedTeamId(team.id);
                setCreatingTeam(false);
                onSaved?.();
              }}
            />
          </div>
        )}
      </div>

      {order.length === 0 ? (
        <p className="muted">
          This project has no volumes yet — register data before building an
          assignment plan.
        </p>
      ) : (
        <div className="table-wrap">
          <table className="task-table plan-table">
            {/* Reading order: which task, where it stands, then what it is made
                of, who has it, and only then the way in. Status is second
                because "where does this stand" is what a manager scans the
                whole table for — on the main row so it never costs a click per
                row, and next to the id so the scan is one narrow column at the
                left edge rather than a sweep across the volume metadata.
                Details is last because it is the only control here that reveals
                more rather than saying something. */}
            <colgroup>
              <col className="col-plan-task" />
              <col className="col-plan-status" />
              <col className="col-plan-volume" />
              <col className="col-plan-format" />
              <col className="col-plan-shape" />
              <col className="col-plan-voxel" />
              <col className="col-plan-coverage" />
              <col className="col-plan-label" />
              <col className="col-plan-assignee" />
              <col className="col-plan-details" />
            </colgroup>
            <thead>
              <tr>
                <th>Task</th>
                <th>Status</th>
                <th>Volume</th>
                <th>Format</th>
                <th>Shape (Z × Y × X)</th>
                <th>Voxel size (Z × Y × X)</th>
                <th>Region coverage</th>
                <th>Label type</th>
                <th>Assignee</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {order.map((id) => {
                const t = meta[id];
                const row = draft[id];
                if (!t || !row) return null;
                const dirty = original[id] && !rowsEqual(row, original[id]);
                const expanded = expandedIds.has(id);
                return (
                  <Fragment key={id}>
                    <tr className={dirty ? "plan-row-dirty" : undefined}>
                      <td className="plan-task-cell">#{t.id}</td>
                      <td className="cell-badge"><StatusBadge value={t.status} /></td>
                      <td className="cell-name volume-name-cell" title={t.volume_name}>
                        {t.volume_name}
                      </td>
                      <td className="mono-cell">{t.file_format || "—"}</td>
                      <td className="mono-cell">{formatShape(t)}</td>
                      <td className="mono-cell">{formatVoxelSize(t)}</td>
                      <td className="cell-badge">
                        <RegionCoverage hasMask={t.has_region_mask} coverage={t.region_mask_coverage} />
                      </td>
                      <td className="cell-badge"><StatusBadge value={t.label_type || "none"} /></td>
                      <td>
                        <select
                          className="plan-assignee-select"
                          aria-label={`Assignee for ${t.volume_name}`}
                          value={row.annotator_id != null && workingMemberIds.has(row.annotator_id) ? row.annotator_id : ""}
                          disabled={busy || !workingTeam}
                          onChange={(event) => patch(id, {
                            annotator_id: event.target.value ? Number(event.target.value) : null,
                          })}
                        >
                          <option value="">{workingTeam ? "(unassigned)" : "Choose a working team first"}</option>
                          {(workingTeam?.members ?? []).map((member) => (
                            <option key={member.user_id} value={member.user_id}>{member.username}</option>
                          ))}
                        </select>
                      </td>
                      <td>
                        {/* Always "Details" — the caret carries open/closed, and
                            `aria-expanded` says it to a screen reader. Swapping
                            the word to "Hide" made the control change identity
                            mid-interaction: the thing you clicked is no longer
                            on screen to click again. */}
                        <button
                          type="button"
                          className="secondary plan-details-toggle"
                          aria-expanded={expanded}
                          aria-controls={`plan-details-${id}`}
                          onClick={() => setExpandedIds((current) => {
                            const next = new Set(current);
                            if (next.has(id)) next.delete(id);
                            else next.add(id);
                            return next;
                          })}
                        >
                          <span className="plan-details-caret" aria-hidden="true">▸</span>
                          Details
                        </button>
                      </td>
                    </tr>
                    {expanded && (
                      <tr id={`plan-details-${id}`} className={`plan-detail-row${dirty ? " plan-row-dirty" : ""}`}>
                        <td colSpan={10}>
                          <div className="plan-detail-grid">
                            <label>
                              <span>Priority</span>
                              <LevelSelect
                                levels={PRIORITY_LEVELS}
                                value={row.priority}
                                disabled={busy}
                                onChange={(value) => patch(id, { priority: value })}
                              />
                            </label>
                            <label>
                              <span>Difficulty</span>
                              <LevelSelect
                                levels={DIFFICULTY_LEVELS}
                                value={row.difficulty}
                                disabled={busy}
                                onChange={(value) => patch(id, { difficulty: value })}
                              />
                            </label>
                            <label>
                              <span>Deadline</span>
                              <input
                                type="date"
                                value={row.deadline}
                                disabled={busy}
                                onChange={(event) => patch(id, { deadline: event.target.value })}
                              />
                            </label>
                            <label className="plan-detail-instructions">
                              <span>Instructions</span>
                              <InstructionsTextarea
                                value={row.instructions}
                                disabled={busy}
                                onChange={(value) => patch(id, { instructions: value })}
                              />
                            </label>
                            <div className="plan-detail-static">
                              <span>Annotations</span>
                              <div className="plan-annotation-actions">
                                <AnnotationLockButton
                                  task={t}
                                  disabled={busy}
                                  onChanged={(locked) => setMeta((current) => ({
                                    ...current,
                                    [t.id]: {...current[t.id], annotation_locked: locked},
                                  }))}
                                />
                                <ResetLabelsButton
                                  taskId={t.id}
                                  volumeName={t.volume_name}
                                  disabled={busy}
                                  onReset={() => void rows.reload()}
                                />
                              </div>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function AnnotationLockButton({
  task,
  disabled,
  onChanged,
}: {
  task: AssignmentPlanTask;
  disabled: boolean;
  onChanged: (locked: boolean) => void;
}) {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nextLocked = !task.annotation_locked;
  return <span className="plan-lock-annotation">
    <button
      type="button"
      className="secondary"
      disabled={disabled || running}
      onClick={async () => {
        setRunning(true);
        setError(null);
        try {
          await setTaskAnnotationLock(task.id, nextLocked);
          onChanged(nextLocked);
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : "Could not change annotation access.");
        } finally {
          setRunning(false);
        }
      }}
    >
      {running ? "Updating…" : task.annotation_locked ? "Reopen annotation" : "Close annotation"}
    </button>
    {error && <span className="error">{error}</span>}
  </span>;
}

/**
 * Give one assignee's task a clean starting mask.
 *
 * Scope is deliberately the *task's working copy* — one volume, one task — not
 * the assignment: a manager handing work back does not want the assignee, the
 * deadline or the instructions thrown away too, only the draft annotation. The
 * volume's registered label mask is what it restores, and is never written to.
 */
function ResetLabelsButton({
  taskId,
  volumeName,
  disabled,
  onReset,
}: {
  taskId: number;
  volumeName: string;
  disabled: boolean;
  onReset: () => void;
}) {
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <span className="plan-reset-labels">
      <button
        type="button"
        className="secondary danger-outline"
        disabled={disabled || running}
        title={`Discard the working annotation for ${volumeName} and restore its registered label mask. Affects every layer, saved and unsaved. The registered source file is never changed.`}
        onClick={async () => {
          if (!window.confirm(
            `Reset the working labels for "${volumeName}" (task #${taskId})?\n\n`
            + "Every annotation on this task's working copy is discarded — all layers, "
            + "saved and unsaved, plus Track prompts and label verification state. "
            + "The assignment itself is kept. The registered source mask is not changed. "
            + "This cannot be undone.",
          )) {
            return;
          }
          setRunning(true);
          setError(null);
          try {
            await resetWorkingLabels(taskId);
            onReset();
          } catch (e) {
            setError(e instanceof Error ? e.message : "Reset failed");
          } finally {
            setRunning(false);
          }
        }}
      >
        {running ? "Resetting…" : "Reset annotations"}
      </button>
      {error && <span className="error">{error}</span>}
    </span>
  );
}
