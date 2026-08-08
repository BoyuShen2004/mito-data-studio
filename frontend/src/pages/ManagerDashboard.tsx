import { Link, useSearchParams } from "react-router-dom";
import { displayTaskLayerRange } from "../features/viewer/layerIndex";
import { listProjects } from "../api/projects";
import { listSubmissions } from "../api/submissions";
import { useAsync } from "../hooks/useAsync";
import StatusBadge from "../components/StatusBadge";
import PublicShareTree from "../components/PublicShareTree";
import SectionTabs, { type SectionTab } from "../components/SectionTabs";
import type { Project } from "../types/project";
import type { Submission } from "../types/submission";
import { submissionChannelLabel } from "../components/TaskDetailsCards";

type DashboardTab = "projects" | "approvals" | "reviews" | "shares";

export default function ManagerDashboard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const projects = useAsync(listProjects, []);
  const submissions = useAsync(() => listSubmissions("submitted"), []);
  const rows = projects.data ?? [];
  const reviewRows = submissions.data ?? [];
  const totalVolumes = rows.reduce((sum, project) => sum + project.volume_count, 0);
  const pendingApproval = rows.filter((project) => !project.manager_reviewed);

  const tabs: SectionTab<DashboardTab>[] = [
    { id: "projects", label: "Projects", count: rows.length },
    ...(pendingApproval.length > 0
      ? [{ id: "approvals" as const, label: "Approvals", count: pendingApproval.length }]
      : []),
    { id: "reviews", label: "Reviews", count: reviewRows.length },
    { id: "shares", label: "Shares" },
  ];
  const requested = searchParams.get("tab") as DashboardTab | null;
  const active = tabs.some((tab) => tab.id === requested)
    ? requested as DashboardTab
    : "projects";
  const selectTab = (tab: DashboardTab) => setSearchParams({ tab });

  return (
    <div className="role-home">
      <header className="page-header row spread">
        <div>
          <h1>Manager Dashboard</h1>
          <p className="muted">Review what needs attention, then move into one workspace.</p>
        </div>
        <div className="row page-actions">
          <Link to="/projects/new"><button>+ New project</button></Link>
          <Link to="/register-data"><button className="secondary">Register data</button></Link>
        </div>
      </header>

      <div className="summary-strip" aria-label="Workspace summary">
        <SummaryMetric label="Projects" value={projects.data ? rows.length : "…"} />
        <SummaryMetric label="Volumes" value={projects.data ? totalVolumes : "…"} />
        <SummaryMetric label="Awaiting approval" value={projects.data ? pendingApproval.length : "…"} tone={pendingApproval.length ? "warn" : undefined} />
        <SummaryMetric label="Waiting reviews" value={submissions.data ? reviewRows.length : "…"} tone={reviewRows.length ? "warn" : undefined} />
      </div>

      <div className="manager-workspace">
        <aside className="attention-rail">
          <section className="attention-panel">
            <div className="eyebrow">Needs attention</div>
            <AttentionLink
              label="Projects to approve"
              count={pendingApproval.length}
              tab="approvals"
              disabled={pendingApproval.length === 0}
            />
            <AttentionLink
              label="Submissions to review"
              count={reviewRows.length}
              tab="reviews"
              disabled={reviewRows.length === 0}
            />
            {pendingApproval.length === 0 && reviewRows.length === 0 && (
              <p className="muted attention-clear">You’re caught up.</p>
            )}
          </section>

          <nav className="shortcut-list" aria-label="Manager shortcuts">
            <div className="eyebrow">Shortcuts</div>
            <Link to="/projects">All projects <span aria-hidden="true">→</span></Link>
            <Link to="/register-data">Register data <span aria-hidden="true">→</span></Link>
            <Link to="/people">People &amp; teams <span aria-hidden="true">→</span></Link>
          </nav>
        </aside>

        <main className="workspace-main">
          <SectionTabs
            tabs={tabs}
            active={active}
            onChange={selectTab}
            label="Manager dashboard panels"
          />
          <section className="workspace-panel" role="tabpanel">
            {active === "projects" && <ProjectsPanel rows={rows} loading={projects.loading} />}
            {active === "approvals" && <ApprovalsPanel rows={pendingApproval} />}
            {active === "reviews" && <ReviewsPanel rows={reviewRows} loading={submissions.loading} />}
            {active === "shares" && <PublicShareTree />}
          </section>
        </main>
      </div>
    </div>
  );
}

function SummaryMetric({ label, value, tone }: { label: string; value: number | string; tone?: "warn" }) {
  return <div className={`summary-metric${tone ? ` summary-metric-${tone}` : ""}`}>
    <strong>{value}</strong><span>{label}</span>
  </div>;
}

function AttentionLink({ label, count, tab, disabled }: { label: string; count: number; tab: DashboardTab; disabled?: boolean }) {
  return disabled ? (
    <span className="attention-link attention-link-disabled"><span>{label}</span><strong>{count}</strong></span>
  ) : (
    <Link className="attention-link" to={`/manager?tab=${tab}`}><span>{label}</span><strong>{count}</strong></Link>
  );
}

function ProjectsPanel({ rows, loading }: { rows: Project[]; loading: boolean }) {
  return <>
    <div className="section-heading row spread">
      <div><h2>Projects</h2><p className="muted">Current delivery and annotation work.</p></div>
      <Link to="/projects">Open project directory</Link>
    </div>
    {loading ? <p className="muted">Loading…</p> : rows.length === 0 ? (
      <div className="empty-state">No projects yet. <Link to="/projects/new">Create the first project</Link>.</div>
    ) : <ProjectTable rows={rows} />}
  </>;
}

function ProjectTable({ rows }: { rows: Project[] }) {
  return <div className="table-wrap"><table>
    <thead><tr><th>Project</th><th>Status</th><th>Volumes</th><th>Tasks</th><th>Deadline</th></tr></thead>
    <tbody>{rows.map((project) => <tr key={project.id}>
      <td className="cell-name"><Link to={`/projects/${project.id}`}>{project.title}</Link></td>
      <td><StatusBadge value={project.status} /></td>
      <td>{project.volume_count}</td><td>{project.task_count}</td><td>{project.deadline ?? "—"}</td>
    </tr>)}</tbody>
  </table></div>;
}

function ApprovalsPanel({ rows }: { rows: Project[] }) {
  return <>
    <div className="section-heading"><h2>Approvals</h2><p className="muted">Requester projects waiting for manager approval.</p></div>
    {rows.length === 0 ? <div className="empty-state">Nothing is waiting for approval.</div> : (
      <div className="table-wrap"><table>
        <thead><tr><th>Project</th><th>Registered by</th><th>Volumes</th><th /></tr></thead>
        <tbody>{rows.map((project) => <tr key={project.id}>
          <td className="cell-name"><Link to={`/projects/${project.id}`}>{project.title}</Link></td>
          <td>{project.created_by_username || "—"}</td><td>{project.volume_count}</td>
          <td><Link to={`/projects/${project.id}?tab=overview`}>Review</Link></td>
        </tr>)}</tbody>
      </table></div>
    )}
  </>;
}

function ReviewsPanel({ rows, loading }: { rows: Submission[]; loading: boolean }) {
  return <>
    <div className="section-heading"><h2>Reviews</h2><p className="muted">Submitted annotations waiting for a decision.</p></div>
    {loading ? <p className="muted">Loading…</p> : rows.length === 0 ? (
      <div className="empty-state">Nothing to review.</div>
    ) : <div className="table-wrap"><table>
      <thead><tr><th>Submission</th><th>Channel</th><th>Task</th><th>Annotator</th><th>QC</th><th>Submitted</th><th /></tr></thead>
      <tbody>{rows.map((submission) => <tr key={submission.id}>
        <td>#{submission.id}</td>
        <td>{submissionChannelLabel(submission.source)}</td>
        <td className="cell-name">{submission.task_detail.volume_name} z{displayTaskLayerRange(submission.task_detail.z_start, submission.task_detail.z_end)}</td>
        <td>{submission.annotator_username}</td><td><StatusBadge value={submission.qc_status} /></td>
        <td>{new Date(submission.submitted_at).toLocaleString()}</td>
        <td><Link to={`/submissions/${submission.id}/review`}>Review</Link></td>
      </tr>)}</tbody>
    </table></div>}
  </>;
}
