import { useState } from "react";
import { ApiError } from "../api/client";
import type { Dependents } from "../api/datasets";

function describe(d: Dependents): string {
  const parts = [
    d.datasets ? `${d.datasets} dataset(s)` : "",
    d.volumes ? `${d.volumes} volume(s)` : "",
    d.tasks ? `${d.tasks} task(s)` : "",
    d.submissions ? `${d.submissions} submission(s)` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "nothing else";
}

/** Delete control that refuses to discard annotation work silently.
 *
 * It asks the server what hangs off the thing first, and a delete carrying
 * tasks or submissions is rejected (409) until the user confirms a second,
 * explicit "delete anyway" — so a stray click cannot destroy annotator output.
 */
export default function DeleteButton({
  label,
  dependents,
  onDelete,
  onDone,
  className = "danger",
}: {
  label: string;
  dependents: () => Promise<Dependents>;
  onDelete: (force: boolean) => Promise<unknown>;
  onDone: () => void;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);

  /** The server's refusal reason and the override share one native dialog. */
  const force = async (reason: string) => {
    if (!window.confirm(`${reason}\n\nPermanently delete ${label} AND its annotation work?\n\nThis cannot be undone.`)) {
      return;
    }
    await onDelete(true);
    onDone();
  };

  const run = async () => {
    setBusy(true);
    try {
      const counts = await dependents();
      if (!window.confirm(`Delete ${label}?\n\nThis also removes ${describe(counts)}.`)) {
        return;
      }
      try {
        await onDelete(false);
      } catch (err) {
        // 409 means the server is protecting existing work; offer the override.
        if (!(err instanceof ApiError && err.status === 409)) throw err;
        await force(err.message);
        return;
      }
      onDone();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <button type="button" className={className} onClick={run} disabled={busy}>
      {busy ? "Deleting…" : "Delete"}
    </button>
  );
}
