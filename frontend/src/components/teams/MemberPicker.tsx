import type {
  CollaborationAnnotator,
  TeamMember,
} from "../../api/collaboration";

export default function MemberPicker({
  label,
  annotators,
  members,
  onAdd,
  onRemove,
  disabled = false,
}: {
  label: string;
  annotators: CollaborationAnnotator[];
  members: TeamMember[];
  onAdd: (userId: number) => void;
  onRemove: (userId: number) => void;
  disabled?: boolean;
}) {
  const memberIds = new Set(members.map((member) => member.user_id));
  const available = annotators.filter((user) => !memberIds.has(user.id));

  return (
    <div className="team-member-picker">
      <label className="field team-member-select">
        <span>Add annotator</span>
        <select
          aria-label={label}
          value=""
          disabled={disabled}
          onChange={(event) => {
            const userId = Number(event.target.value);
            if (userId) onAdd(userId);
          }}
        >
          <option value="">Add annotator…</option>
          {available.map((user) => (
            <option key={user.id} value={user.id}>
              {user.username}
            </option>
          ))}
        </select>
      </label>
      <div className="row team-member-chips" aria-label={`${label} members`}>
        {members.map((member) => (
            <span className="team-member-chip" key={member.user_id}>
              {member.username}
              <button
                type="button"
                className="team-member-remove"
                aria-label={`Remove ${member.username}`}
                disabled={disabled}
                onClick={() => onRemove(member.user_id)}
              >
                × Remove
              </button>
            </span>
        ))}
      </div>
    </div>
  );
}
