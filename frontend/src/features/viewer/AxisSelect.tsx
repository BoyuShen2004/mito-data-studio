import type { Axis } from "../../api/viewer";
import { VIEW_AXIS_OPTIONS } from "./axisView";

/** Shared Axial / Coronal / Sagittal dropdown for View + Annotate (Cellable parity). */
export default function AxisSelect({
  value,
  onChange,
  disabled = false,
  id = "view-axis",
}: {
  value: Axis;
  onChange: (axis: Axis) => void;
  disabled?: boolean;
  id?: string;
}) {
  return (
    <label className="axis-select" htmlFor={id} title="View axis — which dimension you scroll through">
      <span className="muted axis-select-label">Axis</span>
      <select
        id={id}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value as Axis)}
      >
        {VIEW_AXIS_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value} title={opt.title}>
            {opt.label}
          </option>
        ))}
      </select>
    </label>
  );
}
