export type InterpClickAnchor = { label: number; layer: number };

/**
 * Canvas click while Interpolate is active.
 *
 * - Always follows the clicked label as Active.
 * - First click on a label anchors that label on the current layer.
 * - Second click on the **same** label on a **different** layer sets Start/End
 *   to the earlier/later layer indices (ordered, not click order).
 * - A new label resets the anchor and clears endpoints until the pair completes.
 */
export function applyInterpolateCanvasClick(
  label: number,
  layer: number,
  anchor: InterpClickAnchor | null,
  interpFirst: number | null,
  interpLast: number | null,
): {
  activeId: number;
  anchor: InterpClickAnchor | null;
  interpFirst: number | null;
  interpLast: number | null;
} | null {
  if (label <= 0) return null;

  if (anchor == null || anchor.label !== label) {
    return {
      activeId: label,
      anchor: { label, layer },
      interpFirst: null,
      interpLast: null,
    };
  }

  if (anchor.layer === layer) {
    return {
      activeId: label,
      anchor,
      interpFirst,
      interpLast,
    };
  }

  const lo = Math.min(anchor.layer, layer);
  const hi = Math.max(anchor.layer, layer);
  return {
    activeId: label,
    anchor: null,
    interpFirst: lo,
    interpLast: hi,
  };
}
