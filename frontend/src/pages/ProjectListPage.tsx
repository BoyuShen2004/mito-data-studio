import { Link } from "react-router-dom";
import { listProjects } from "../api/projects";
import { useAsync } from "../hooks/useAsync";

import WorkList, { useWorkFilter } from "../components/WorkList";

/** Every project, on the same list component the task and case lists use.
 * `Register data` lives here rather than in the navbar: it is an action, not a
 * place. */
export default function ProjectListPage() {
  const { data, loading, error } = useAsync(listProjects, []);
  const [filter, setFilter] = useWorkFilter();

  return (
    <>
      <div className="row spread">
        <h1>Projects</h1>
        <div className="row page-actions">
          <Link to="/register-data">
            <button type="button" className="secondary">Register data</button>
          </Link>
          <Link to="/projects/new">
            <button type="button">+ New project</button>
          </Link>
        </div>
      </div>

      {error && <div className="error">{error}</div>}
      <WorkList
        kind="project"
        rows={data ?? []}
        loading={loading}
        filter={filter}
        onFilterChange={setFilter}
        label="Projects"
        emptyText={<>No projects yet. <Link to="/projects/new">Create the first one</Link>, then register data into it.</>}
      />
    </>
  );
}
