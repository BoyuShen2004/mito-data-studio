import { useState } from "react";
import { Link } from "react-router-dom";
import { getPeopleOverview, updateMyProfile } from "../api/people";
import { useAsync } from "../hooks/useAsync";
import { useAuth } from "../auth/AuthContext";
import { roleLabel } from "../labels";
import StatusBadge from "../components/StatusBadge";
import AnnotatorTimeSection from "../components/AnnotatorTimeSection";
import CollaborationManager from "../components/CollaborationManager";
import type { Person, PersonStats } from "../types/people";

/**
 * People — one app section, role-specific panels.
 *
 * The backend (`accounts/services.py`) already projects the shared-project
 * relation into `managers` / `peers` / `annotators` / `requesters`, so this
 * page renders whichever of those came back non-empty rather than branching on
 * role in three places. Every role gets the same editable profile card at top.
 */
export default function PeoplePage() {
  const { user, refresh } = useAuth();
  const overview = useAsync(getPeopleOverview, []);

  if (overview.loading) return <p className="muted">Loading…</p>;
  if (overview.error) return <div className="error">{overview.error}</div>;
  if (!overview.data) return null;
  const d = overview.data;
  const isManager = d.role === "manager";
  const isRequester = d.role === "requester" || d.role === "client";

  return (
    <>
      <div className="row spread">
        <h1>People</h1>
        <span className="muted">
          Signed in as {user?.username} ({roleLabel(d.role)})
        </span>
      </div>

      <p className="muted">
        People is the home for project rosters and teams. Adding an annotator
        through a project&rsquo;s Access page or its working team now grants both
        browse access and assignment eligibility.
      </p>

      <ProfileCard
        me={d.me}
        onSaved={() => {
          void refresh();
          overview.reload();
        }}
      />

      {isManager ? (
        <>
          <CollaborationManager />
          <PeopleSection
            title="Annotators"
            hint="Everyone doing the work: current load, how often they have handed it over, and your last decision on it."
            people={d.annotators}
            statKeys={ANNOTATOR_STAT_KEYS}
          />
          <PeopleSection
            title="Customers (requesters)"
            hint="Who asked for the work, and the projects they registered."
            people={d.requesters}
            statKeys={REQUESTER_STAT_KEYS}
          />
        </>
      ) : isRequester ? (
        <>
          <RequesterProjects projects={d.projects} />
          <PeopleSection
            title="Managers on your projects"
            hint="Who to talk to about scope, timing, or delivery."
            people={d.managers}
          />
          <PeopleSection
            title="Annotators working on your data"
            people={d.peers}
          />
        </>
      ) : (
        <>
          <PeopleSection
            title="Your manager(s)"
            hint="Derived from the projects you hold tasks on — this is who reviews your submissions."
            people={d.managers}
          />
          <PeopleSection
            title="Annotators on your projects"
            hint="People you share a project with."
            people={d.peers}
            statKeys={[["shared_projects", "Shared projects"]]}
          />
          <MyProjects projects={d.projects} />
        </>
      )}
    </>
  );
}

/**
 * All project participants use the same read surface; the API enforces scope.
 */
export function ProjectRef({
  id,
  title,
}: {
  id: number;
  title: string;
}) {
  return <Link to={`/projects/${id}`}>{title}</Link>;
}

// Which stats to surface, and what to call them. Anything not listed stays out
// of the card — the backend returns more than a roster needs to show.
type StatKeys = [string, string][];

const ANNOTATOR_STAT_KEYS: StatKeys = [
  ["assigned", "Tasks"],
  ["active", "Active"],
  ["submitted", "Awaiting review"],
  ["approved", "Approved"],
  ["rejected", "Sent back"],
  ["submissions", "Submissions"],
  ["hard_cases_open", "Open hard cases"],
];

const REQUESTER_STAT_KEYS: StatKeys = [
  ["projects", "Projects"],
  ["active_projects", "Active"],
];

function ProfileCard({ me, onSaved }: { me: Person; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [displayName, setDisplayName] = useState(me.display_name);
  const [institution, setInstitution] = useState(me.institution_name);
  const [note, setNote] = useState(me.contact_note);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await updateMyProfile({
        display_name: displayName,
        institution_name: institution,
        contact_note: note,
      });
      setEditing(false);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save your profile.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <div className="row spread">
        <h3 style={{ margin: 0 }}>Your profile</h3>
        <button
          type="button"
          className="secondary"
          onClick={() => setEditing((e) => !e)}
        >
          {editing ? "Cancel" : "Edit"}
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      {editing ? (
        <div className="edit-form" style={{ margin: 0 }}>
          <div className="row fields">
            <label className="field">
              <span>Display name</span>
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder={me.username}
              />
            </label>
            <label className="field">
              <span>Lab / institution</span>
              <input
                value={institution}
                onChange={(e) => setInstitution(e.target.value)}
              />
            </label>
          </div>
          <label className="field">
            <span>Contact note</span>
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={280}
              placeholder="How teammates should reach you, working hours, …"
            />
          </label>
          <button type="button" onClick={() => void save()} disabled={busy}>
            {busy ? "Saving…" : "Save profile"}
          </button>
        </div>
      ) : (
        <table>
          <tbody>
            <tr>
              <th>Name</th>
              <td>
                {me.display_name || me.username}{" "}
                <span className="muted">({me.username})</span>
              </td>
            </tr>
            <tr>
              <th>Role</th>
              <td>{roleLabel(me.role)}</td>
            </tr>
            <tr>
              <th>Lab / institution</th>
              <td>{me.institution_name || "—"}</td>
            </tr>
            <tr>
              <th>Contact note</th>
              <td>{me.contact_note || "—"}</td>
            </tr>
          </tbody>
        </table>
      )}
      {me.stats && Object.keys(me.stats).length > 0 && (
        <StatRow stats={me.stats} keys={statKeysFor(me.stats)} />
      )}
    </div>
  );
}

/** Show whatever numeric stats came back, prettified — the "me" card's stats
 * differ per role and are not worth a third hard-coded list. */
function statKeysFor(stats: PersonStats): StatKeys {
  return Object.keys(stats)
    .filter((k) => typeof stats[k] === "number")
    .map((k) => [k, k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())]);
}

function StatRow({ stats, keys }: { stats: PersonStats; keys: StatKeys }) {
  const shown = keys.filter(([key]) => stats[key] != null);
  if (shown.length === 0) return null;
  return (
    <div className="people-stats">
      {shown.map(([key, label]) => (
        <span key={key} className="people-stat">
          <strong>{String(stats[key])}</strong> {label}
        </span>
      ))}
    </div>
  );
}

function PeopleSection({
  title,
  hint,
  people,
  statKeys,
}: {
  title: string;
  hint?: string;
  people: Person[];
  statKeys?: StatKeys;
}) {
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      {hint && <p className="muted">{hint}</p>}
      {people.length === 0 ? (
        <p className="muted" style={{ marginBottom: 0 }}>
          Nobody here yet.
        </p>
      ) : (
        <div className="people-grid">
          {people.map((p) => (
            <PersonCard key={p.id} person={p} statKeys={statKeys} />
          ))}
        </div>
      )}
    </div>
  );
}

export function PersonCard({
  person,
  statKeys,
}: {
  person: Person;
  statKeys?: StatKeys;
}) {
  const last = person.stats?.last_decision;
  return (
    <div className="people-card">
      <div className="row spread">
        <strong>
          <Link to={`/people/${person.username}`}>
            {person.display_name || person.username}
          </Link>
        </strong>
        <span className="muted">{roleLabel(person.role)}</span>
      </div>
      <div className="muted" style={{ fontSize: "0.78rem" }}>
        {person.username}
        {person.institution_name ? ` · ${person.institution_name}` : ""}
      </div>
      {person.contact_note && (
        <div className="muted" style={{ fontSize: "0.78rem" }}>
          {person.contact_note}
        </div>
      )}
      {statKeys && person.stats && (
        <StatRow stats={person.stats} keys={statKeys} />
      )}
      {typeof last === "string" && last !== "" && (
        <div style={{ marginTop: "0.35rem" }}>
          <span className="muted" style={{ fontSize: "0.78rem" }}>
            Last decision{" "}
          </span>
          <StatusBadge value={last} />
        </div>
      )}
      {person.projects && person.projects.length > 0 && (
        <ul className="people-projects">
          {person.projects.slice(0, 5).map((p) => (
            <li key={p.id}>
              <ProjectRef id={p.id} title={p.title} />
            </li>
          ))}
          {person.projects.length > 5 && (
            <li className="muted">+{person.projects.length - 5} more</li>
          )}
        </ul>
      )}
      {/* Directly under the Projects list, collapsed. The roster renders many
          of these and most are never opened, so the request only fires on
          expand — see AnnotatorTimeSection. */}
      <AnnotatorTimeSection username={person.username} />
    </div>
  );
}

function MyProjects({ projects }: { projects: Person["projects"] }) {
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Your projects</h3>
      {!projects || projects.length === 0 ? (
        <p className="muted" style={{ marginBottom: 0 }}>
          You have no tasks on any project yet.
        </p>
      ) : (
        <ul className="people-projects">
          {projects.map((p) => (
            <li key={p.id}>
              <ProjectRef id={p.id} title={p.title} />{" "}
              <StatusBadge value={p.status} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RequesterProjects({ projects }: { projects: Person["projects"] }) {
  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Your projects</h3>
      {!projects || projects.length === 0 ? (
        <p className="muted" style={{ marginBottom: 0 }}>
          You have not registered any projects yet.
        </p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Project</th>
                <th>Status</th>
                <th>Manager(s)</th>
                <th>Tasks</th>
                <th>Deadline</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <tr key={p.id}>
                  <td>
                    <ProjectRef id={p.id} title={p.title} />
                  </td>
                  <td>
                    <StatusBadge value={p.status} />
                  </td>
                  <td>{(p.managers ?? []).join(", ") || "—"}</td>
                  <td>{p.task_count ?? "—"}</td>
                  <td>{p.deadline ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
