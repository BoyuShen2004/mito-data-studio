import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  addProjectMember,
  getProjectSummary,
  listProjectMembers,
  removeProjectMember,
  reviewProject,
} from "../api/projects";
import { listAnnotators } from "../api/tasks";
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
import HardCaseList from "../components/HardCaseList";
import StatusBadge from "../components/StatusBadge";
import ShareControl from "../components/ShareControl";
import SectionTabs, { type SectionTab } from "../components/SectionTabs";
import type { Volume } from "../types/volume";
import type { WorkloadRow } from "../types/project";
import { DatasetVolumesTable } from "../components/VolumeMeta";

type ProjectTab = "overview" | "data" | "assign" | "access" | "activity";

export default function ProjectDetailPage() {
  const { id } = useParams();
  const projectId = Number(id);
  const { isManager, isRequester, user } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const summary = useAsync(() => getProjectSummary(projectId), [projectId]);
  const volumes = useAsync(() => listProjectVolumes(projectId), [projectId]);
  const deployment = useAsync(getDeploymentIdentity, []);

  const [reviewing, setReviewing] = useState(false);
  const [editing, setEditing] = useState(false);

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
  const tabs: SectionTab<ProjectTab>[] = isManager
    ? [
        { id: "overview", label: "Overview" },
        { id: "data", label: "Data", count: project.volume_count },
        { id: "assign", label: "Assign", count: project.task_count },
        { id: "access", label: "Access" },
        { id: "activity", label: "Activity" },
      ]
    : [
        { id: "overview", label: "Overview" },
        { id: "data", label: "Data", count: project.volume_count },
        { id: "activity", label: "Activity" },
      ];
  const requested = searchParams.get("tab") as ProjectTab | null;
  const active = tabs.some((tab) => tab.id === requested)
    ? requested as ProjectTab
    : "overview";
  const selectTab = (tab: ProjectTab) => setSearchParams({ tab });

  return (
    <div className="project-page">
      <header className="project-header row spread">
        <div>
          <div className="row project-title-line"><h1>{project.title}</h1><StatusBadge value={project.status} /></div>
          <p className="muted">
            {project.dataset_count} dataset{project.dataset_count === 1 ? "" : "s"} ·{" "}
            {project.annotation_type.replace(/_/g, " ")} · {project.annotation_target} · deadline {project.deadline ?? "—"}
          </p>
        </div>
        <div className="row project-header-actions">
          {isManager && <ShareControl scope="project" projectId={project.id} />}
          {(isManager || project.created_by === user?.id) && <button
            type="button"
            className="secondary"
            onClick={() => setEditing((v) => !v)}
          >
            {editing ? "Close" : "Edit project"}
          </button>}
          {(isManager || project.created_by === user?.id) && <DeleteButton
            label={`project "${project.title}"`}
            dependents={() => projectDependents(projectId)}
            onDelete={(force) => deleteProjectForce(projectId, force)}
            onDone={() => navigate("/projects")}
          />}
        </div>
      </header>

      {editing && (
        <ProjectEditForm
          project={project}
          onSaved={() => {
            setEditing(false);
            summary.reload();
          }}
        />
      )}

      <SectionTabs tabs={tabs} active={active} onChange={selectTab} label="Project sections" sticky />

      <main className="project-pane" role="tabpanel">
        {active === "overview" && <>
          <ReviewBanner reviewed={reviewed} isManager={isManager} busy={reviewing} onReview={doReview} />
          {deployment.data?.features.FEATURE_DASHBOARDS === true
            ? <ProjectOperationalStatistics projectId={projectId} />
            : <ProjectSummaryCard progress={progress} />}
          <div className="next-actions row">
            <span className="eyebrow">Next</span>
            <button type="button" className="secondary" onClick={() => selectTab("data")}>Open data</button>
            {isManager && reviewed && <button type="button" className="secondary" onClick={() => selectTab("assign")}>Assign work</button>}
            {isRequester && <Link to={`/register-data?project=${projectId}`}><button type="button">Register more data</button></Link>}
          </div>
        </>}

        {active === "data" && <>
          <DatasetsCard datasets={project.datasets ?? []} volumes={volumes.data ?? []} projectId={projectId} onChanged={reloadAll} />
          {(volumes.data ?? []).some((volume) => !volume.dataset) && <section className="section-block">
            <div className="section-heading"><h2>Ungrouped volumes</h2><p className="muted">Volumes registered before dataset grouping was available.</p></div>
            <VolumeList volumes={volumes} />
          </section>}
        </>}

        {active === "assign" && isManager && <section className="section-block">
          <div className="section-heading"><h2>Assignment &amp; task metadata</h2><p className="muted">Push one assignee per volume and set task priority, difficulty, deadline, or instructions.</p></div>
          {!reviewed ? <div className="empty-state">Approve this project in Overview before assigning work.</div> : (
            <AssignmentPlanEditor
              projectId={projectId}
              projectTitle={project.title}
              workingTeamId={project.working_team}
              projectDeadline={project.deadline}
              onSaved={reloadAll}
            />
          )}
        </section>}

        {active === "access" && isManager && <>
          <section className="section-block access-share-note">
            <div><h2>Public access</h2><p className="muted">The compact Share control remains in the project header so its state is visible from every pane.</p></div>
          </section>
          <ProjectMembers projectId={projectId} />
          <p className="muted">Team eligibility is managed in <Link to="/people">People</Link>.</p>
        </>}

        {active === "activity" && <>
          <ProjectHardCases projectId={projectId} />
          {isManager && <WorkloadTable workload={workload} />}
        </>}
      </main>
    </div>
  );
}

function ProjectMembers({ projectId }: { projectId: number }) {
  const members = useAsync(() => listProjectMembers(projectId), [projectId]);
  const annotators = useAsync(listAnnotators, []);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const memberIds = new Set((members.data ?? []).map((row) => row.user_id));

  const add = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await addProjectMember(projectId, Number(selected));
      setSelected("");
      members.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add member.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (userId: number) => {
    setBusy(true);
    setError(null);
    try {
      await removeProjectMember(projectId, userId);
      members.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not remove member.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="section-block">
      <div className="section-heading"><h2>Access members</h2></div>
      <p className="muted">
        Members can view the project and Hard Cases without being assigned a
        task. Work assignment stays separate in the Assign tab.
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
      {error && <div className="error">{error}</div>}
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

/** Hard cases flagged on this project, newest first. Same list body as the
 * `/hard-cases` inbox — the project page is just a pre-filtered view of it. */
function ProjectHardCases({ projectId }: { projectId: number }) {
  const cases = useAsync(() => listHardCases({ project: projectId }), [projectId]);
  const rows = cases.data ?? [];
  const open = rows.filter((c) => c.status === "open");

  return (
    <section className="section-block">
      <div className="row spread">
        <h2 style={{ margin: 0 }}>Hard cases ({open.length})</h2>
        <Link to="/hard-cases">
          <button type="button" className="secondary">
            All hard cases
          </button>
        </Link>
      </div>
      {cases.loading ? (
        <p className="muted">Loading…</p>
      ) : cases.error ? (
        <div className="error">{cases.error}</div>
      ) : (
        <HardCaseList
          cases={rows}
          showProject={false}
          onChanged={cases.reload}
          emptyText="Nobody has flagged a hard case on this project yet."
        />
      )}
    </section>
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
