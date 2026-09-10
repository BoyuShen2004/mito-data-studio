import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  addProjectMember,
  getProjectSummary,
  listProjectMembers,
  removeProjectMember,
  reviewProject,
} from "../api/projects";
import { listAnnotators, listProjectTasks } from "../api/tasks";
import { getDeploymentIdentity } from "../api/deployment";
import { getProjectStatistics } from "../api/statistics";
import { deleteProjectForce, projectDependents } from "../api/datasets";
import { listProjectVolumes } from "../api/volumes";
import { listHardCases } from "../api/hardCases";
import { useAuth } from "../auth/AuthContext";
import { useAsync, type AsyncState } from "../hooks/useAsync";
import ProjectSummaryCard from "../components/ProjectSummaryCard";
import DatasetsCard from "../components/DatasetsCard";
import DeleteButton from "../components/DeleteButton";
import ProjectEditForm from "../components/ProjectEditForm";
import AssignmentPlanEditor from "../components/AssignmentPlanEditor";
import WorkList, { useWorkFilter } from "../components/WorkList";
import Breadcrumb from "../components/Breadcrumb";
import StatusBadge from "../components/StatusBadge";
import ShareControl, { ShareSummary } from "../components/ShareControl";
import SectionTabs, { type SectionTab } from "../components/SectionTabs";

import type { Volume } from "../types/volume";
import type { Project, WorkloadRow } from "../types/project";
import type { HardCase } from "../types/hardCase";
import { DatasetVolumesTable } from "../components/VolumeMeta";

/** Nouns, not verbs — "Assign" was the odd one out and is now a bulk action
 * inside Tasks; "Activity" was a junk drawer and its two halves went to
 * Overview (workload) and Cases (hard cases). */
type ProjectTab = "overview" | "data" | "tasks" | "cases" | "people" | "settings";

export default function ProjectDetailPage() {
  const { id } = useParams();
  const projectId = Number(id);
  const { isManager, user } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const summary = useAsync(() => getProjectSummary(projectId), [projectId]);
  const volumes = useAsync(() => listProjectVolumes(projectId), [projectId]);
  const deployment = useAsync(getDeploymentIdentity, []);
  // Loaded at page level because the tab strip shows the open count; the
  // rows themselves are the same payload the Cases tab renders.
  const hardCases = useAsync(() => listHardCases({ project: projectId }), [projectId]);

  const [reviewing, setReviewing] = useState(false);

  const reloadAll = () => {
    summary.reload();
    volumes.reload();
  };

  const doReview = async (reviewed: boolean) => {
    setReviewing(true);
    try {
      await reviewProject(projectId, reviewed);
      summary.reload();
    } catch (e) {
      // Surface via alert so the review banner layout stays put.
      window.alert(e instanceof Error ? e.message : "Review update failed");
    } finally {
      setReviewing(false);
    }
  };

  if (summary.loading) return <p className="muted">Loading…</p>;
  if (summary.error) return <div className="error">{summary.error}</div>;
  if (!summary.data) return null;

  const { project, progress, workload } = summary.data;
  const reviewed = project.manager_reviewed;
  const canEditProject = isManager || project.created_by === user?.id;
  const tabs: SectionTab<ProjectTab>[] = [
    { id: "overview", label: "Overview" },
    { id: "data", label: "Data", count: project.volume_count },
    { id: "tasks", label: "Tasks", count: project.task_count },
    { id: "cases", label: "Cases", count: hardCases.data?.filter((row) => row.status === "open").length },
    ...(isManager ? [{ id: "people" as const, label: "People" }] : []),
    ...(canEditProject ? [{ id: "settings" as const, label: "Settings" }] : []),
  ];
  const requested = searchParams.get("tab") as ProjectTab | null;
  const active = tabs.some((tab) => tab.id === requested)
    ? requested as ProjectTab
    : "overview";
  // A tab switch drops the previous list's filters: a stale `?assignee=` from
  // Tasks would otherwise hide every row on Cases.
  const selectTab = (tab: ProjectTab) => setSearchParams({ tab });

  return (
    <div className="project-page">
      <Breadcrumb items={[{ label: "Projects", to: "/projects" }, { label: project.title }]} />

      <header className="project-header row spread">
        <div>
          <div className="row project-title-line"><h1>{project.title}</h1><StatusBadge value={project.status} /></div>
          <p className="muted">
            {project.dataset_count} dataset{project.dataset_count === 1 ? "" : "s"} ·{" "}
            {project.annotation_type.replace(/_/g, " ")} · {project.annotation_target} · deadline {project.deadline ?? "—"}
          </p>
        </div>
      </header>

      <SectionTabs tabs={tabs} active={active} onChange={selectTab} label="Project sections" sticky />

      <main className="project-pane" role="tabpanel">
        {active === "overview" && <>
          <ReviewBanner reviewed={reviewed} isManager={isManager} busy={reviewing} onReview={doReview} />
          {deployment.data?.features.FEATURE_DASHBOARDS === true
            ? <ProjectOperationalStatistics projectId={projectId} />
            : <ProjectSummaryCard progress={progress} />}
          {isManager && <WorkloadTable workload={workload} />}
          {/* Overview reports what is shared; the buttons that change it are
              in Settings. */}
          {isManager && <section className="section-block">
            <div className="section-heading"><h2>Public access</h2></div>
            <ShareSummary scope="project" projectId={projectId} />
          </section>}
        </>}

        {active === "data" && <>
          <div className="row spread section-heading">
            <div><h2>Data</h2><p className="muted">Datasets and the volumes registered into them.</p></div>
            {/* Only the roles `/register-data` actually admits; anyone else
                would be bounced straight back home by the route guard. */}
            {canEditProject && (
              <Link to={`/register-data?project=${projectId}`}><button type="button">Add data</button></Link>
            )}
          </div>
          <DatasetsCard datasets={project.datasets ?? []} volumes={volumes.data ?? []} projectId={projectId} onChanged={reloadAll} />
          {(volumes.data ?? []).some((volume) => !volume.dataset) && <section className="section-block">
            <div className="section-heading"><h2>Ungrouped volumes</h2><p className="muted">Volumes registered before dataset grouping was available.</p></div>
            <VolumeList volumes={volumes} />
          </section>}
        </>}

        {active === "tasks" && (
          <ProjectTasks
            projectId={projectId}
            projectTitle={project.title}
            workingTeamId={project.working_team}
            projectDeadline={project.deadline}
            reviewed={reviewed}
            isManager={isManager}
            onSaved={reloadAll}
          />
        )}

        {active === "cases" && <ProjectHardCases cases={hardCases} />}

        {active === "people" && isManager && <>
          <ProjectMembers projectId={projectId} />
          <p className="muted">Team eligibility is managed in <Link to="/people">People</Link>.</p>
        </>}

        {active === "settings" && canEditProject && (
          <ProjectSettings
            project={project}
            isManager={isManager}
            onSaved={summary.reload}
            onDeleted={() => navigate("/projects")}
          />
        )}
      </main>
    </div>
  );
}

function ProjectMembers({ projectId }: { projectId: number }) {
  const members = useAsync(() => listProjectMembers(projectId), [projectId]);
  const annotators = useAsync(listAnnotators, []);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const memberIds = new Set((members.data ?? []).map((row) => row.user_id));

  const add = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      await addProjectMember(projectId, Number(selected));
      setSelected("");
      members.reload();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Could not add member.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (userId: number) => {
    setBusy(true);
    try {
      await removeProjectMember(projectId, userId);
      members.reload();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Could not remove member.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="section-block">
      <div className="section-heading"><h2>Access members</h2></div>
      <p className="muted">
        Members can view the project and Hard Cases without being assigned a
        task. Adding an annotator here also adds them to this project&rsquo;s
        working team so they can be assigned work. Removing Access removes
        browse-only membership; remove them from the working team in People to
        remove assignment eligibility.
      </p>
      <div className="row">
        <select value={selected} onChange={(e) => setSelected(e.target.value)}>
          <option value="">Add an annotator…</option>
          {(annotators.data ?? [])
            .filter((person) => !memberIds.has(person.id))
            .map((person) => (
              <option key={person.id} value={person.id}>{person.username}</option>
            ))}
        </select>
        <button type="button" onClick={add} disabled={!selected || busy}>
          Add member
        </button>
      </div>
      {members.loading ? <p className="muted">Loading members…</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Person</th><th>Access</th><th>Workload</th><th /></tr></thead>
            <tbody>
              {(members.data ?? []).map((row) => (
                <tr key={row.user_id}>
                  <td>{row.display_name || row.username}</td>
                  <td>{row.access_reason}</td>
                  <td>{row.has_tasks ? "Has task work" : "No tasks"}</td>
                  <td>
                    {row.is_explicit && (
                      <button className="secondary" type="button" disabled={busy}
                        onClick={() => remove(row.user_id)}>Remove membership</button>
                    )}
                  </td>
                </tr>
              ))}
              {(members.data ?? []).length === 0 && (
                <tr><td colSpan={4} className="muted">No annotators participate yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function WorkloadTable({ workload }: { workload: WorkloadRow[] | undefined }) {
  return <section className="section-block">
    <div className="section-heading"><h2>Annotator workload</h2><p className="muted">Assigned work on this project, grouped by person.</p></div>
    {!workload || workload.length === 0 ? <div className="empty-state">No assigned work yet.</div> : (
      <div className="table-wrap"><table>
        <thead><tr><th>Annotator</th><th>Active</th><th>Submitted</th><th>Approved</th><th>Total</th></tr></thead>
        <tbody>{workload.map((row) => <tr key={row.annotator_id}>
          <td>{row.username}</td><td>{row.active}</td><td>{row.submitted}</td><td>{row.approved}</td><td>{row.total}</td>
        </tr>)}</tbody>
      </table></div>
    )}
  </section>;
}

function duration(value: number | null) {
  if (value == null) return "—";
  if (value < 3600) return `${Math.round(value / 60)} min`;
  if (value < 86400) return `${(value / 3600).toFixed(1)} h`;
  return `${(value / 86400).toFixed(1)} d`;
}

function ProjectOperationalStatistics({ projectId }: { projectId: number }) {
  const stats = useAsync(() => getProjectStatistics(projectId), [projectId]);
  if (stats.loading) return <div className="section-block muted">Loading workflow statistics…</div>;
  if (stats.error || !stats.data) return null;
  const data = stats.data;
  return (
    <section className="section-block">
      <div className="section-heading"><h2>Workflow statistics</h2></div>
      <div className="summary-strip">
        <div className="summary-metric"><strong>{data.tasks.percent_complete}%</strong><span>Complete</span></div>
        <div className="summary-metric"><strong>{data.tasks.total}</strong><span>Tasks</span></div>
        <div className="summary-metric"><strong>{data.tasks.approved}</strong><span>Approved</span></div>
        <div className="summary-metric"><strong>{data.reviews.rejection_rate == null ? "—" : `${Math.round(data.reviews.rejection_rate * 100)}%`}</strong><span>Rejection rate</span></div>
        <div className="summary-metric"><strong>{duration(data.elapsed.mean_elapsed_to_approve_seconds)}</strong><span>Mean review time</span></div>
      </div>
    </section>
  );
}

/** Hard cases flagged on this project — the same `WorkList` the task list and
 * the personal home use, with the project scope applied at the endpoint and
 * state/category/author narrowed in the browser. */
function ProjectHardCases({ cases }: { cases: AsyncState<HardCase[]> }) {
  const [filter, setFilter] = useWorkFilter();

  return (
    <section className="section-block">
      <div className="section-heading"><h2>Cases</h2><p className="muted">Labels the team flagged as hard, and what was decided about them.</p></div>
      {cases.error ? (
        <div className="error">{cases.error}</div>
      ) : (
        <WorkList
          kind="case"
          rows={cases.data ?? []}
          loading={cases.loading}
          showProject={false}
          filter={filter}
          onFilterChange={setFilter}
          onChanged={cases.reload}
          label="Hard cases on this project"
          emptyText={
            <>Nobody has flagged a hard case on this project yet. They are raised from the Annotate toolbar with “Record hard case”.</>
          }
        />
      )}
    </section>
  );
}

/**
 * The project's work list, and the assignment editor as a bulk action inside
 * it rather than a tab of its own.
 *
 * "Assign volumes" with rows ticked edits exactly those; with nothing ticked
 * it opens the whole project's plan, which is what the old Assign tab did.
 */
function ProjectTasks({
  projectId,
  projectTitle,
  workingTeamId,
  projectDeadline,
  reviewed,
  isManager,
  onSaved,
}: {
  projectId: number;
  projectTitle: string;
  workingTeamId: number | null;
  projectDeadline: string | null;
  reviewed: boolean;
  isManager: boolean;
  onSaved: () => void;
}) {
  const tasks = useAsync(() => listProjectTasks(projectId), [projectId]);
  const [filter, setFilter] = useWorkFilter();
  const [selected, setSelected] = useState<number[]>([]);
  const [assigning, setAssigning] = useState<number[] | null>(null);
  const canAssign = isManager && reviewed;
  // A plain helper, not a component: one declared inside a render body gets a
  // new type identity every render and remounts the button under the click.
  const assignButton = (ids: number[]) => (
    <button type="button" onClick={() => setAssigning(ids)}>Assign volumes</button>
  );

  if (assigning) {
    return (
      <section className="section-block">
        <div className="row spread section-heading">
          <div>
            <h2>Assign volumes</h2>
            <p className="muted">
              {assigning.length > 0
                ? `${assigning.length} selected task${assigning.length === 1 ? "" : "s"}.`
                : "Every task on this project."}{" "}
              Push one assignee per volume and set priority, difficulty, deadline, or instructions.
            </p>
          </div>
          <button type="button" className="secondary" onClick={() => setAssigning(null)}>
            Back to tasks
          </button>
        </div>
        <AssignmentPlanEditor
          projectId={projectId}
          projectTitle={projectTitle}
          workingTeamId={workingTeamId}
          projectDeadline={projectDeadline}
          restrictToTaskIds={assigning.length > 0 ? assigning : undefined}
          onSaved={() => {
            tasks.reload();
            onSaved();
          }}
        />
      </section>
    );
  }

  return (
    <section className="section-block">
      <div className="row spread section-heading">
        <div><h2>Tasks</h2><p className="muted">One task per volume, one assignee each.</p></div>
        {canAssign && assignButton(selected)}
      </div>
      {!reviewed && isManager && (
        <div className="empty-state">Approve this project in Overview before assigning work.</div>
      )}
      {tasks.error ? (
        <div className="error">{tasks.error}</div>
      ) : (
        <WorkList
          kind="task"
          rows={tasks.data ?? []}
          loading={tasks.loading}
          showProject={false}
          filter={filter}
          onFilterChange={setFilter}
          onChanged={tasks.reload}
          label="Tasks on this project"
          selectable={canAssign}
          selected={selected}
          onSelectionChange={setSelected}
          bulkActions={assignButton}
          emptyText={
            <>No tasks yet. Register volumes under <strong>Data</strong>, then use <strong>Assign volumes</strong>.</>
          }
        />
      )}
    </section>
  );
}

/** Everything that changes the project itself, in one place: edit, share, and
 * — quarantined at the bottom — delete. */
function ProjectSettings({
  project,
  isManager,
  onSaved,
  onDeleted,
}: {
  project: Project;
  isManager: boolean;
  onSaved: () => void;
  onDeleted: () => void;
}) {
  return (
    <>
      <section className="section-block">
        <div className="section-heading"><h2>Project details</h2></div>
        <ProjectEditForm project={project} onSaved={onSaved} />
      </section>

      {isManager && (
        <section className="section-block">
          <div className="section-heading">
            <h2>Public access</h2>
            <p className="muted">A public link makes this project readable without an account.</p>
          </div>
          <ShareControl scope="project" projectId={project.id} />
        </section>
      )}

      <section className="danger-zone">
        <h2>Danger zone</h2>
        <p className="muted">
          Deleting a project removes its datasets, volumes, tasks and hard cases. There is no undo.
        </p>
        <DeleteButton
          label={`project "${project.title}"`}
          dependents={() => projectDependents(project.id)}
          onDelete={(force) => deleteProjectForce(project.id, force)}
          onDone={onDeleted}
        />
      </section>
    </>
  );
}

function ReviewBanner({
  reviewed,
  isManager,
  busy,
  onReview,
}: {
  reviewed: boolean;
  isManager: boolean;
  busy: boolean;
  onReview: (reviewed: boolean) => void;
}) {
  if (reviewed) {
    // Approved state needs no banner tip — only managers keep Undo.
    if (!isManager) return null;
    return (
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <button
          className="secondary"
          onClick={() => onReview(false)}
          disabled={busy}
        >
          Undo review
        </button>
      </div>
    );
  }
  return (
    <div className="card" style={{ borderColor: "var(--warn)" }}>
      <div className="row spread">
        <span>
          <StatusBadge value="in_review" />{" "}
          <span className="muted">
            {isManager
              ? "Requester-registered data awaiting your review before assignment."
              : "Awaiting manager review before annotation can be assigned."}
          </span>
        </span>
        {isManager && (
          <button onClick={() => onReview(true)} disabled={busy}>
            {busy ? "Saving…" : "Approve & enable assignment"}
          </button>
        )}
      </div>
    </div>
  );
}


function VolumeList({
  volumes,
}: {
  volumes: AsyncState<Volume[]>;
}) {
  return (
    <div className="table-wrap">
      {volumes.loading ? (
        <p className="muted">Loading…</p>
      ) : (volumes.data ?? []).length === 0 ? (
        <p className="muted">
          No volumes registered.
        </p>
      ) : (
        <DatasetVolumesTable
          volumes={volumes.data ?? []}
          actionLabel="Details"
          action={(item) => <Link to={`/volumes/${(item as Volume).id}`}>Details</Link>}
        />
      )}
    </div>
  );
}
