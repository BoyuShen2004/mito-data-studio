import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getCollaboration, mutateCollaboration } from "../api/collaboration";
import { listProjects } from "../api/projects";
import { useAsync } from "../hooks/useAsync";
import TeamEditor from "./teams/TeamEditor";

export default function CollaborationManager() {
  const [searchParams] = useSearchParams();
  const projectId = Number(searchParams.get("project")) || null;
  const collaboration = useAsync(getCollaboration, []);
  const projects = useAsync(listProjects, []);
  const [error, setError] = useState("");
  const currentProject = (projects.data ?? []).find((project) => project.id === projectId);

  const run = async (body: Record<string, unknown>) => {
    setError("");
    try {
      await mutateCollaboration(body);
      collaboration.reload();
      projects.reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };
  const data = collaboration.data;
  const annotators = (data?.users ?? []).filter((user) => user.role === "annotator");

  const deleteTeam = (team: NonNullable<typeof data>["teams"][number]) => {
    const impact = team.delete_impact;
    const projectList = (impact?.projects ?? [])
      .map((project) => `${project.title} (${project.task_count} assigned)`)
      .join(", ");
    const consequence = impact?.project_count
      ? `\n\nThis is the working team for ${impact.project_count} project(s): ${projectList}. ` +
        `${impact.task_count} assignment(s) will be withdrawn, current working masks promoted to official labels, and annotators will see cancelled Done items.`
      : "\n\nThis team has no working project assignments.";
    if (!window.confirm(`Delete team “${team.name}”?${consequence}`)) return;
    void run({ action: "delete_team", team_id: team.id, confirm: true });
  };

  return (
    <div className="card">
      <h3>Teams &amp; assignment eligibility</h3>
      <p className="muted">
        A team is just a name and its annotators. {currentProject
          ? `A new team here becomes the working team for ${currentProject.title}.`
          : "Choose it as a project’s working team when its members should receive that project’s volumes."}
      </p>
      <TeamEditor
        annotators={annotators}
        teams={data?.teams ?? []}
        defaultName={currentProject?.title ?? ""}
        projectId={projectId ?? undefined}
        onChanged={() => {
          collaboration.reload();
          projects.reload();
        }}
      />
      {(data?.teams ?? []).map((team) => (
        <div className="card" key={team.id}>
          <TeamEditor
            team={team}
            annotators={annotators}
            onChanged={() => collaboration.reload()}
          />
          <div className="row">
            <span className="muted">Working project:</span>
            {(projects.data ?? []).map((project) => {
              const working = project.working_team === team.id;
              return (
                <button
                  type="button"
                  className={working ? "" : "secondary"}
                  key={project.id}
                  disabled={working}
                  onClick={() => void run({
                    action: "set_project_working_team",
                    project_id: project.id,
                    team_id: team.id,
                  })}
                >
                  {working ? `✓ ${project.title}` : `Use for ${project.title}`}
                </button>
              );
            })}
            <button
              type="button"
              className="danger secondary"
              onClick={() => deleteTeam(team)}
            >
              Delete team
            </button>
          </div>
        </div>
      ))}
      {!collaboration.loading && (data?.teams.length ?? 0) === 0 && (
        <p className="muted">No teams yet.</p>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  );
}
