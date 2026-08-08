import { useState } from "react";
import { Link } from "react-router-dom";
import { listProjects } from "../api/projects";
import { useAsync } from "../hooks/useAsync";
import StatusBadge from "../components/StatusBadge";
import LifecycleTabs from "../features/lifecycle/LifecycleTabs";
import { getLifecycleCounts } from "../features/lifecycle/api";
import { lifecycleLabel, type Lifecycle } from "../labels";

export default function RequesterDashboard() {
  const [lifecycle, setLifecycle] = useState<Lifecycle | "all">("all");
  const projects = useAsync(
    () => listProjects(lifecycle === "all" ? undefined : lifecycle),
    [lifecycle],
  );
  const counts = useAsync(getLifecycleCounts, []);
  const rows = projects.data ?? [];
  const volumes = rows.reduce((sum, project) => sum + project.volume_count, 0);
  const awaitingReview = rows.filter((project) => !project.manager_reviewed).length;

  return (
    <div className="role-home">
      <header className="page-header row spread">
        <div>
          <h1>My Projects</h1>
          <p className="muted">Register datasets and follow them through manager review and annotation.</p>
        </div>
        <div className="row page-actions">
          <Link to="/projects/new"><button>+ New project</button></Link>
          <Link to="/register-data"><button className="secondary">Register data</button></Link>
        </div>
      </header>

      <div className="summary-strip" aria-label="Project summary">
        <div className="summary-metric"><strong>{projects.data ? rows.length : "…"}</strong><span>Projects</span></div>
        <div className="summary-metric"><strong>{projects.data ? volumes : "…"}</strong><span>Volumes</span></div>
        <div className={`summary-metric${awaitingReview ? " summary-metric-warn" : ""}`}><strong>{projects.data ? awaitingReview : "…"}</strong><span>Awaiting review</span></div>
      </div>

      <div className="requester-workspace">
        <main className="workspace-main">
          <div className="section-heading"><h2>Registered projects</h2><p className="muted">Filter by lifecycle, then open a project for its data and activity.</p></div>
          <LifecycleTabs active={lifecycle} counts={counts.data ?? undefined} onChange={setLifecycle} />
          {projects.loading ? <p className="muted">Loading…</p> : rows.length === 0 ? (
            <div className="empty-state">No projects here yet. <Link to="/register-data">Register data</Link> to get started.</div>
          ) : (
            <div className="table-wrap"><table>
              <thead><tr><th>Project</th><th>Lifecycle</th><th>Status</th><th>Manager review</th><th>Volumes</th><th>Created</th></tr></thead>
              <tbody>{rows.map((project) => <tr key={project.id}>
                <td className="cell-name"><Link to={`/projects/${project.id}`}>{project.title}</Link></td>
                <td>{lifecycleLabel(project.lifecycle)}</td>
                <td><StatusBadge value={project.status} /></td>
                <td><StatusBadge value={project.manager_reviewed ? "approved" : "in_review"} /></td>
                <td>{project.volume_count}</td>
                <td>{new Date(project.created_at).toLocaleDateString()}</td>
              </tr>)}</tbody>
            </table></div>
          )}
        </main>

        <aside className="requester-next-step">
          <div className="eyebrow">Add data</div>
          <h2>Start with a project</h2>
          <p className="muted">Create the container, then register image, region, and label data into it.</p>
          <Link to="/projects/new"><button type="button">Create project</button></Link>
          <Link to="/register-data" className="secondary-link">Register into an existing project</Link>
        </aside>
      </div>
    </div>
  );
}
