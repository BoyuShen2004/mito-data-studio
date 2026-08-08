import { Link, useParams } from "react-router-dom";
import { getPerson } from "../api/people";
import { useAsync } from "../hooks/useAsync";
import { roleLabel } from "../labels";
import StatusBadge from "../components/StatusBadge";
import { ProjectRef } from "./PeoplePage";

/** Read-only card for one person, reached from any People roster.
 * Shows nothing the `/people` overview doesn't already show — it just puts
 * one person on their own page so a link is shareable. */
export default function PersonPage() {
  const { username } = useParams();
  const person = useAsync(() => getPerson(username as string), [username]);

  if (person.loading) return <p className="muted">Loading…</p>;
  if (person.error) return <div className="error">{person.error}</div>;
  if (!person.data) return null;
  const p = person.data;
  const stats = p.stats ?? {};
  const numericStats = Object.entries(stats).filter(
    ([, v]) => typeof v === "number",
  );

  return (
    <>
      <div className="row spread">
        <h1>{p.display_name || p.username}</h1>
        <Link to="/people">
          <button type="button" className="secondary">
            All people
          </button>
        </Link>
      </div>

      <div className="card">
        <table>
          <tbody>
            <tr>
              <th>Username</th>
              <td>{p.username}</td>
            </tr>
            <tr>
              <th>Role</th>
              <td>{roleLabel(p.role)}</td>
            </tr>
            <tr>
              <th>Lab / institution</th>
              <td>{p.institution_name || "—"}</td>
            </tr>
            <tr>
              <th>Contact note</th>
              <td>{p.contact_note || "—"}</td>
            </tr>
            {typeof stats.last_decision === "string" && stats.last_decision && (
              <tr>
                <th>Last review decision</th>
                <td>
                  <StatusBadge value={stats.last_decision} />
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {numericStats.length > 0 && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Workload</h3>
          <div className="people-stats">
            {numericStats.map(([key, value]) => (
              <span key={key} className="people-stat">
                <strong>{String(value)}</strong>{" "}
                {key.replace(/_/g, " ")}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="card">
        <h3 style={{ marginTop: 0 }}>Projects</h3>
        {(p.projects ?? []).length === 0 ? (
          <p className="muted" style={{ marginBottom: 0 }}>
            No projects.
          </p>
        ) : (
          <ul className="people-projects">
            {(p.projects ?? []).map((proj) => (
              <li key={proj.id}>
                <ProjectRef id={proj.id} title={proj.title} />{" "}
                <StatusBadge value={proj.status} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
