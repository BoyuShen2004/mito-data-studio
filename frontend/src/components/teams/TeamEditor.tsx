import { useEffect, useState } from "react";
import {
  mutateCollaboration,
  type CollaborationAnnotator,
  type CollaborationState,
  type CollaborationTeam,
  type TeamMember,
} from "../../api/collaboration";
import MemberPicker from "./MemberPicker";

export default function TeamEditor({
  annotators,
  teams = [],
  team,
  defaultName = "",
  projectId,
  onChanged,
  onCreated,
}: {
  annotators: CollaborationAnnotator[];
  teams?: CollaborationTeam[];
  team?: CollaborationTeam;
  defaultName?: string;
  projectId?: number;
  onChanged?: (state: CollaborationState) => void;
  onCreated?: (team: CollaborationTeam, state: CollaborationState) => void;
}) {
  const [name, setName] = useState(team?.name ?? defaultName);
  const [newMemberIds, setNewMemberIds] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setName(team?.name ?? defaultName);
    if (!team) setNewMemberIds([]);
  }, [team?.id, team?.name, defaultName]);

  const members: TeamMember[] = team
    ? team.members
    : newMemberIds.flatMap((userId) => {
        const user = annotators.find((row) => row.id === userId);
        return user ? [{ user_id: user.id, username: user.username }] : [];
      });

  const run = async (body: Record<string, unknown>) => {
    setBusy(true);
    setError("");
    try {
      const state = await mutateCollaboration(body);
      onChanged?.(state);
      return state;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    const existingIds = new Set(teams.map((row) => row.id));
    const state = await run({
      action: "create_team",
      name,
      member_ids: newMemberIds,
      ...(projectId ? { project_id: projectId } : {}),
    });
    if (!state) return;
    const returnedTeams = state.teams ?? [];
    const createdId = typeof state.mutation?.team_id === "number"
      ? state.mutation.team_id
      : null;
    const created = returnedTeams.find((row) => row.id === createdId)
      ?? returnedTeams.find((row) => !existingIds.has(row.id))
      ?? returnedTeams.find((row) => row.name === name.trim())
      ?? returnedTeams[returnedTeams.length - 1];
    if (created) onCreated?.(created, state);
    setName(defaultName);
    setNewMemberIds([]);
  };

  const add = (userId: number) => {
    if (team) {
      void run({ action: "add_team_member", team_id: team.id, user_id: userId });
      return;
    }
    setNewMemberIds((current) =>
      current.includes(userId) ? current : [...current, userId],
    );
  };

  const remove = (userId: number) => {
    if (team) {
      void run({ action: "remove_team_member", team_id: team.id, user_id: userId });
      return;
    }
    setNewMemberIds((current) => current.filter((id) => id !== userId));
  };

  return (
    <div className="team-editor">
      <div className="team-editor-controls">
        <label className="field team-name-field">
          <span>Team name</span>
          <input
            aria-label={team ? `Team name for ${team.name}` : "New team name"}
            placeholder="Team name"
            value={name}
            disabled={busy}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <MemberPicker
          label={team ? `Add annotator to ${team.name}` : "Add annotator to new team"}
          annotators={annotators}
          members={members}
          onAdd={add}
          onRemove={remove}
          disabled={busy}
        />
        <button
          type="button"
          className={team ? "secondary team-editor-action" : "team-editor-action"}
          disabled={busy || !name.trim() || (Boolean(team) && name.trim() === team?.name)}
          onClick={() => {
            if (team) {
              void run({ action: "rename_team", team_id: team.id, name });
            } else {
              void create();
            }
          }}
        >
          {team ? "Rename" : "Create team"}
        </button>
      </div>
      {error && <div className="error">{error}</div>}
    </div>
  );
}
