/**
 * Why an instance was flagged as a hard case.
 *
 * Mirrors `core.choices.HardCaseCategory`. Kept as one list so the record
 * dialog, the list filter, and the detail editor cannot drift apart — and so
 * adding a reason is one edit on each side rather than three on this one.
 *
 * `""` is a real value: cases recorded before the field existed carry none,
 * and the UI shows them as "Uncategorised" rather than guessing.
 */
export type HardCaseCategory =
  | ""
  | "uncertain"
  | "needs_split"
  | "needs_merge"
  | "false_positive"
  | "boundary_truncated"
  | "other";

export const HARD_CASE_CATEGORIES: {
  value: Exclude<HardCaseCategory, "">;
  label: string;
  hint: string;
}[] = [
  {
    value: "uncertain",
    label: "Uncertain",
    hint: "Needs a second opinion",
  },
  {
    value: "needs_split",
    label: "Needs split",
    hint: "One id covers two objects",
  },
  {
    value: "needs_merge",
    label: "Needs merge",
    hint: "One object split across ids",
  },
  {
    value: "false_positive",
    label: "Not a mitochondrion",
    hint: "Should not be labelled at all",
  },
  {
    value: "boundary_truncated",
    label: "Cut off",
    hint: "Continues outside the volume",
  },
  { value: "other", label: "Other", hint: "Something else" },
];

export const UNCATEGORISED_LABEL = "Uncategorised";

export function categoryLabel(value: string | null | undefined): string {
  if (!value) return UNCATEGORISED_LABEL;
  return (
    HARD_CASE_CATEGORIES.find((entry) => entry.value === value)?.label ?? value
  );
}
