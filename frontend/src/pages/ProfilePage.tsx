import { useMemo, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { updateMyProfile } from "../api/people";
import { roleLabel } from "../labels";
import { shortcutModifierLabel } from "../features/viewer/annotate/shortcutKeys";

// Same paired rows as the canvas context menu, followed by its Verify | Solo
// action row. Nulls deliberately hold the open cells beside single tools.
const SHORTCUT_LAYOUT: readonly (string | null)[] = [
  "select", null,
  "brush", "eraser",
  "box_mask", "box_eraser",
  "point_mask", "boundary",
  "seeds", null,
  "interpolate", "flood_fill",
  "split_3d", "merge",
  "delete", null,
  "verify", "solo",
];

/**
 * Your own account: the short profile the People page already showed, plus the
 * annotate tool shortcuts.
 *
 * The shortcuts are stored **per user on the server**, not in this browser, so
 * they follow the account to a second machine — which is the whole point of
 * customising them rather than memorising someone else's defaults.
 *
 * Only the modifier is not customisable: it is Cmd on macOS and Ctrl elsewhere,
 * so one saved profile works on both. The label below reflects the machine the
 * page is open on.
 */
export default function ProfilePage() {
  const { user, refresh } = useAuth();
  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [contactNote, setContactNote] = useState(user?.contact_note ?? "");
  const [shortcuts, setShortcuts] = useState<Record<string, string>>(
    () => ({ ...(user?.annotate_shortcuts ?? {}) }),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const tools = user?.annotate_shortcut_tools ?? [];
  const canCustomize = Boolean(user?.can_customize_shortcuts);
  const modifier = shortcutModifierLabel();

  /** Which tools currently share a letter. The server refuses to store a
   * conflict, so showing it here is what stops the save from being the first
   * time anyone hears about it. */
  const conflicts = useMemo(() => {
    const byLetter = new Map<string, string[]>();
    for (const { tool } of tools) {
      const letter = (shortcuts[tool] ?? "").toLowerCase();
      if (!letter) continue;
      byLetter.set(letter, [...(byLetter.get(letter) ?? []), tool]);
    }
    const clashing = new Set<string>();
    for (const owners of byLetter.values()) {
      if (owners.length > 1) for (const tool of owners) clashing.add(tool);
    }
    return clashing;
  }, [shortcuts, tools]);

  if (!user) return null;

  const save = async () => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await updateMyProfile({
        display_name: displayName,
        contact_note: contactNote,
        ...(canCustomize ? { annotate_shortcuts: shortcuts } : {}),
      });
      await refresh();
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save your profile");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="profile-page">
      <h1>Profile</h1>
      <p className="muted">
        {user.username} · {roleLabel(user.role)}
        {user.institution_name ? ` · ${user.institution_name}` : ""}
      </p>

      <section className="card profile-section">
        <h2>About you</h2>
        <label className="profile-field">
          <span>Display name</span>
          <input
            type="text"
            value={displayName}
            maxLength={150}
            placeholder={user.username}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </label>
        <label className="profile-field">
          <span>Contact note</span>
          <input
            type="text"
            value={contactNote}
            maxLength={280}
            placeholder="How teammates should reach you"
            onChange={(e) => setContactNote(e.target.value)}
          />
        </label>
      </section>

      <section className="card profile-section">
        <h2>Annotate shortcuts</h2>
        {canCustomize ? (
          <>
            <p className="muted">
              Hold <kbd>{modifier}</kbd> and press the letter to switch tool while
              annotating. One letter per tool; leave a box empty for no shortcut.
              Saved to your account, so it follows you between browsers.
            </p>
            <div className="profile-shortcut-grid" aria-label="Annotate shortcut bindings">
              {SHORTCUT_LAYOUT.map((tool, cell) => {
                if (tool == null) return <span key={`gap-${cell}`} className="profile-shortcut-gap" aria-hidden="true" />;
                const entry = tools.find((item) => item.tool === tool);
                if (!entry) return null;
                const { label } = entry;
                return (
                <label
                  key={tool}
                  className={`profile-shortcut${conflicts.has(tool) ? " conflict" : ""}`}
                >
                  <span className="profile-shortcut-tool">{label}</span>
                  <span className="profile-shortcut-modifier">{modifier}</span>
                  <input
                    type="text"
                    className="profile-shortcut-key"
                    aria-label={`${label} shortcut letter`}
                    value={(shortcuts[tool] ?? "").toUpperCase()}
                    maxLength={1}
                    onChange={(e) => {
                      const letter = e.target.value.replace(/[^a-zA-Z]/g, "").toLowerCase();
                      setSaved(false);
                      setShortcuts((current) => ({ ...current, [tool]: letter }));
                    }}
                  />
                </label>
                );
              })}
            </div>
            {conflicts.size > 0 && (
              <p className="error profile-shortcut-error" role="alert">
                Two tools cannot share a letter — one shortcut, one tool. Change one
                of the highlighted boxes.
              </p>
            )}
            <button
              type="button"
              className="secondary"
              onClick={() => {
                setSaved(false);
                setShortcuts({ ...(user.annotate_shortcut_defaults ?? {}) });
              }}
            >
              Reset to defaults
            </button>
          </>
        ) : (
          <p className="muted">
            {roleLabel(user.role)} accounts do not annotate, so there are no tool
            shortcuts to set.
          </p>
        )}
      </section>

      <div className="row profile-actions">
        <button type="button" onClick={save} disabled={saving || conflicts.size > 0}>
          {saving ? "Saving…" : "Save profile"}
        </button>
        {saved && <span className="muted" role="status">Saved.</span>}
        {error && <span className="error">{error}</span>}
      </div>
    </div>
  );
}
