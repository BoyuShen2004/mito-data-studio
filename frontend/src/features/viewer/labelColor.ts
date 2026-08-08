// Deterministic, well-spread RGB for an instance id — mirrors the backend's
// `_label_color` (annotation/visualization/slice_io.py) so the 2D overlay,
// the Labels panel swatches, and the 3D preview all agree on one color per
// id. Shared by AnnotationCanvas/LabelsPanel/Labels3DPanel — previously
// duplicated inline in AnnotationCanvas only; pulled out once a second and
// third consumer needed the same function.

export function labelColor(id: number): [number, number, number] {
  if (id <= 0) return [0, 0, 0];
  const h = (Math.imul(id, 2654435761) >>> 0) & 0xffffff;
  return [(h >>> 16) & 0xff, (h >>> 8) & 0xff, h & 0xff];
}

export function labelColorCss(id: number): string {
  const [r, g, b] = labelColor(id);
  return `rgb(${r}, ${g}, ${b})`;
}
