import { Link } from "react-router-dom";
import { listProjects } from "../api/projects";
import { useAsync } from "../hooks/useAsync";
import StatusBadge from "../components/StatusBadge";

export default function ProjectListPage() {
  const { data, loading, error } = useAsync(listProjects, []);

  return (
    <>
      <div className="row spread">
        <h1>Projects</h1>
        <Link to="/projects/new">
          <button>+ New project</button>
        </Link>
      </div>

      <p className="muted">Create and open project containers here. Register Data handles ingestion; People handles teams and assignment eligibility.</p>

      {error && <div className="error">{error}</div>}
      <div className="card">
        {loading ? (
          <p className="muted">Loading…</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Status</th>
                  <th>Volumes</th>
                  <th>Tasks</th>
                  <th>Deadline</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {(data ?? []).map((p) => (
                  <tr key={p.id}>
                    <td>
                      <Link to={`/projects/${p.id}`}>{p.title}</Link>
                    </td>
                    <td>
                      <StatusBadge value={p.status} />
                    </td>
                    <td>{p.volume_count}</td>
                    <td>{p.task_count}</td>
                    <td>{p.deadline ?? "—"}</td>
                    <td>{new Date(p.created_at).toLocaleDateString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
