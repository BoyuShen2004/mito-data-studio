import { useEffect, useRef, useState } from "react";

interface Props {
  value: number;
  min: number;
  max: number;
  onCommit: (n: number) => void;
  /** Shown after the field (e.g. "%"). */
  suffix?: string;
  className?: string;
  title?: string;
  /** Accessible name, when the surrounding label text is not enough. */
  ariaLabel?: string;
  /** Width hint for the text field. */
  widthCh?: number;
}

/**
 * Typeable number field that commits on Enter/blur and clamps to [min, max].
 * Keeps a local draft so the user can clear and retype without fighting
 * controlled re-renders mid-edit.
 *
 * The draft is re-synced from `value` only while the field is **not focused**.
 * Syncing unconditionally defeated the draft's whole purpose: a background
 * update to `value` — a slice finishing its load, a layer change arriving from
 * elsewhere — landed between the user's keystroke and their Enter/blur and
 * silently replaced what they had typed, so the commit applied the old number.
 * Rare by hand, reliable under load, and it is why the viewer's layer field
 * intermittently ignored a typed layer.
 */
export default function CommitNumberInput({
  value,
  min,
  max,
  onCommit,
  suffix,
  className = "commit-num",
  title,
  ariaLabel,
  widthCh = 4,
}: Props) {
  const [draft, setDraft] = useState(String(value));
  // Whether the user is mid-edit. A ref, not state: it must not itself cause a
  // render, and it is only ever read inside the sync effect and the handlers.
  const editing = useRef(false);

  useEffect(() => {
    // While the field has focus the draft belongs to the user, not to the
    // prop. It is reconciled on blur, which is also when `commit` runs.
    if (editing.current) return;
    setDraft(String(value));
  }, [value]);

  const commit = () => {
    editing.current = false;
    const n = Number(draft);
    if (!Number.isFinite(n)) {
      setDraft(String(value));
      return;
    }
    const clamped = Math.max(min, Math.min(max, Math.round(n)));
    onCommit(clamped);
    setDraft(String(clamped));
  };

  return (
    <span className="commit-num-wrap">
      <input
        type="text"
        inputMode="numeric"
        className={className}
        value={draft}
        title={title}
        aria-label={ariaLabel}
        style={{ width: `${widthCh}ch` }}
        onFocus={() => {
          editing.current = true;
        }}
        onChange={(e) => {
          // Typing counts as editing even if the field never fired focus —
          // `fireEvent.change` in tests, and programmatic input in a browser,
          // both reach here without a focus event.
          editing.current = true;
          setDraft(e.target.value.replace(/[^\d]/g, ""));
        }}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
          if (e.key === "Escape") {
            editing.current = false;
            setDraft(String(value));
            e.currentTarget.blur();
          }
        }}
      />
      {suffix != null && <span className="commit-num-suffix">{suffix}</span>}
    </span>
  );
}
