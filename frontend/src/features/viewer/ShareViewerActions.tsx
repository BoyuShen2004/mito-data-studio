import { useCallback, useState } from "react";

import type { AxisControls } from "./AnnotationCanvas";
import AxisSelect from "./AxisSelect";
import RegionOnlyButton from "./RegionOnlyButton";

/**
 * The read-only share topbar's actions cluster — the same `[Axis] [Region only]`
 * pair `ViewerPage` puts in `.editor-actions`, minus every authenticated
 * control (no Annotate, no Submit, no Share).
 *
 * All three public surfaces (`/share/public/:token`, `/share/tasks/:token`,
 * `/share/hard-case/:token`) mount the same `AnnotationCanvas` the authenticated
 * View route does, but none of them used to pass `onAxisControls` — so the
 * canvas published its axis state to nobody and recipients got no way to change
 * view axis or clip to the region, even though the canvas has supported both all
 * along. This exists so that wiring is written once rather than three times and
 * cannot drift between the surfaces.
 *
 * `RegionOnlyButton` renders nothing when `controls.hasRegion` is false, so the
 * button appears exactly when the volume meta reports a region mask.
 */
export default function ShareViewerActions({
  controls,
  id,
}: {
  controls: AxisControls | null;
  /** Distinct per surface — `AxisSelect` uses it to bind its own `<label>`. */
  id: string;
}) {
  if (!controls) return null;
  return (
    <div className="editor-actions">
      <AxisSelect
        id={id}
        value={controls.axis}
        onChange={controls.changeAxis}
        disabled={controls.disabled}
      />
      <RegionOnlyButton controls={controls} />
    </div>
  );
}

/**
 * State + a stable callback for the above.
 *
 * The callback identity matters: `AnnotationCanvas` lists `onAxisControls` in
 * the dependencies of the effect that publishes the handle, so an inline arrow
 * would re-run that effect on every render and thrash the topbar.
 */
export function useShareAxisControls() {
  const [controls, setControls] = useState<AxisControls | null>(null);
  const onAxisControls = useCallback((next: AxisControls | null) => {
    setControls(next);
  }, []);
  return { controls, onAxisControls };
}
