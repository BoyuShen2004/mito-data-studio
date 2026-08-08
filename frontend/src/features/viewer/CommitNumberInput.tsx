import { useEffect, useState } from "react";

interface Props {
  value: number;
  min: number;
  max: number;
  onCommit: (n: number) => void;
  /** Shown after the field (e.g. "%"). */
  suffix?: string;
  className?: string;
  title?: string;
  /** Width hint for the text field. */
  widthCh?: number;
}

/**
 * Typeable number field that commits on Enter/blur and clamps to [min, max].
 * Keeps a local draft so the user can clear and retype without fighting
 * controlled re-renders mid-edit.
 */
export default function CommitNumberInput({
  value,
  min,
  max,
  onCommit,
  suffix,
  className = "commit-num",
  title,
  widthCh = 4,
}: Props) {
  const [draft, setDraft] = useState(String(value));

  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  const commit = () => {
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
        style={{ width: `${widthCh}ch` }}
        onChange={(e) => setDraft(e.target.value.replace(/[^\d]/g, ""))}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
          if (e.key === "Escape") {
            setDraft(String(value));
            e.currentTarget.blur();
          }
        }}
      />
      {suffix != null && <span className="commit-num-suffix">{suffix}</span>}
    </span>
  );
}
