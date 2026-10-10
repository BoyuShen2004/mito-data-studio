import { useSearchParams } from "react-router-dom";
import { getCollaboration, mutateCollaboration } from "../api/collaboration";
import { listProjects } from "../api/projects";
import { useAsync } from "../hooks/useAsync";
import TeamEditor from "./teams/TeamEditor";
import Disclosure from "./Disclosure";
import { showError } from "../errorPopup";

export default function CollaborationManager() {
  const [searchParams] = useSearchParams();
  const projectId = Number(searchParams.get("project")) || null;
  const collaboration = useAsync(getCollaboration, []);
  const projects = useAsync(listProjects, []);
  const currentProject = (projects.data ?? []).find((project) => project.id === projectId);

  const run = async (body: Record<string, unknown>) => {
    try {
      await mutateCollaboration(body);
      collaboration.reload();
      projects.reload();
    } catch (reason) {
      showError(reason instanceof Error ? reason.message : String(reason));
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
    <>
      {collaboration.error && <p role="alert" className="error">{collaboration.error} <button type="button" onClick={collaboration.reload}>Retry</button></p>}
      {projects.error && <p role="alert" className="error">{projects.error} <button type="button" onClick={projects.reload}>Retry projects</button></p>}
    <Disclosure className="card" title="Teams & assignment eligibility" count={data?.teams.length} collapsible={data ? Boolean(data.teams.length) : true}
      summary={collaboration.loading ? "Refreshing teams…" : undefined}>
      <p className="muted">
        {currentProject
          ? `New teams grant browse access and assignment eligibility for ${currentProject.title}.`
          : "Working-team members can browse the project and receive assignments."}
        {" "}Removing a team member ends assignment eligibility; explicit project access remains.
      </p>
      <Disclosure title="New team" collapsible={false}>
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
      </Disclosure>
      {(data?.teams ?? []).map((team) => (
        <Disclosure className="card" key={team.id} title={team.name} collapsible={(data?.teams.length ?? 0) > 1}
          summary={`${team.members.length} member${team.members.length === 1 ? "" : "s"} · ${(projects.data ?? []).filter(project => project.working_team === team.id).length} working projects`}>
          {team.organization_name && <p className="muted">Organization: {team.organization_name}</p>}
          {team.description && <p className="muted">{team.description}</p>}
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
        </Disclosure>
      ))}
      {!collaboration.loading && (data?.teams.length ?? 0) === 0 && (
        <p className="muted">No teams yet.</p>
      )}
    </Disclosure>
    </>
  );
}
