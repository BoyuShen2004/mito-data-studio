import { useCallback, useEffect, useState } from "react";
import type { Axis, RegionIndex } from "../../api/viewer";
import { loadRegionIndex, nearestRegionIndex } from "./regionIndex";

/**
 * Jump the current axis to the nearest slice that holds any region.
 *
 * Only rendered when the volume has a region mask. The plane list is fetched
 * in the background on mount. The process/session caches make that first scan
 * reusable, while the viewer remains interactive during it. After the answer
 * the button also knows when it would
 * do nothing (this plane already has region, or no plane does) and says so
 * instead of pretending to act.
 */
export default function JumpToRegionButton({
  volumeId,
  axis,
  index,
  hasRegion,
  getRegionIndex,
  onJump,
  disabled = false,
}: {
  volumeId: number;
  axis: Axis;
  index: number;
  hasRegion: boolean;
  getRegionIndex: (volumeId: number, axis: Axis) => Promise<RegionIndex>;
  onJump: (index: number) => void;
  /** The canvas disables navigation while a save is in flight. */
  disabled?: boolean;
}) {
  const [indices, setIndices] = useState<number[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // An index list belongs to one (volume, axis); showing another axis's would
  // send the viewer to a plane that has no region on this one.
  useEffect(() => {
    setIndices(null);
    setError(null);
    if (!hasRegion) return;
    let alive = true;
    // Prefetch is deliberately non-blocking: the button remains clickable and
    // joins the same de-duplicated promise if the user gets there first.
    // Revalidate on each mount/axis change. sessionStorage is only a fast
    // click fallback; it must never outlive a rebuilt server-side mask.
    void loadRegionIndex(volumeId, axis, getRegionIndex, { refresh: true })
      .then((known) => {
        if (alive) setIndices(known);
      })
      .catch(() => {
        // Background failures stay quiet; a click retries and surfaces them.
      });
    return () => {
      alive = false;
    };
  }, [volumeId, axis, getRegionIndex, hasRegion]);

  const jump = useCallback(async () => {
    setError(null);
    let known = indices;
    if (known === null) {
      setLoading(true);
      try {
        known = await loadRegionIndex(volumeId, axis, getRegionIndex);
        setIndices(known);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not read the region layer");
        return;
      } finally {
        setLoading(false);
      }
    }
    const target = nearestRegionIndex(known, index);
    if (target === null) return; // Already on region, or there is none.
    onJump(target);
  }, [axis, getRegionIndex, index, indices, onJump, volumeId]);

  if (!hasRegion) return null;

  const nothingToDo = indices !== null && nearestRegionIndex(indices, index) === null;
  const alreadyHere = Boolean(indices?.includes(index));
  const title = error
    ? error
    : alreadyHere
      ? "This layer already has region"
      : nothingToDo
        ? "No layer of this axis has region"
        : "Go to the nearest layer that has region";

  return (
    <button
      type="button"
      className="secondary"
      title={title}
      disabled={disabled || loading || nothingToDo}
      onMouseDown={(e) => e.preventDefault()}
      onClick={() => void jump()}
    >
      {loading ? "Jump to region…" : "Jump to region"}
    </button>
  );
}
