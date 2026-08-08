import type { ReactNode } from "react";
import { clampPct } from "./displayAdjust";
import CommitNumberInput from "./CommitNumberInput";

interface Props {
  brightness: number;
  contrast: number;
  onBrightness: (n: number) => void;
  onContrast: (n: number) => void;
  /** Committed-label overlay opacity, 0–100 (100 = fully opaque, Cellable's
   * `label_opacity_slider` default) — #29 item U5. Optional so other
   * DisplayKnobs consumers (if any appear later) aren't forced to wire it. */
  labelOpacity?: number;
  onLabelOpacity?: (n: number) => void;
  /** Immutable region/reference-mask overlay opacity. */
  regionOpacity?: number;
  onRegionOpacity?: (n: number) => void;
  roiOnly?: boolean;
  onRoiOnly?: (enabled: boolean) => void;
  /** Optional controls on the right of this row (e.g. Fit window / Fit width). */
  trailing?: ReactNode;
}

/** Labeled brightness/contrast (+ optional label opacity): slider + typeable 0–100% (50% = normal, opacity 100% = normal). */
export default function DisplayKnobs({
  brightness,
  contrast,
  onBrightness,
  onContrast,
  labelOpacity,
  onLabelOpacity,
  regionOpacity,
  onRegionOpacity,
  roiOnly,
  onRoiOnly,
  trailing,
}: Props) {
  return (
    <div className="display-knobs">
      <label className="display-knob" title="Brightness (0–100%, 50% is normal)">
        <span className="display-knob-label">Brightness</span>
        <input
          type="range"
          min={0}
          max={100}
          value={brightness}
          onChange={(e) => onBrightness(Number(e.target.value))}
        />
        <CommitNumberInput
          value={brightness}
          min={0}
          max={100}
          suffix="%"
          title="Brightness percent"
          onCommit={(n) => onBrightness(clampPct(n))}
        />
      </label>
      <label className="display-knob" title="Contrast (0–100%, 50% is normal)">
        <span className="display-knob-label">Contrast</span>
        <input
          type="range"
          min={0}
          max={100}
          value={contrast}
          onChange={(e) => onContrast(Number(e.target.value))}
        />
        <CommitNumberInput
          value={contrast}
          min={0}
          max={100}
          suffix="%"
          title="Contrast percent"
          onCommit={(n) => onContrast(clampPct(n))}
        />
      </label>
      {labelOpacity != null && onLabelOpacity && (
        <label className="display-knob" title="Committed label overlay opacity (0–100%, 100% is fully opaque)">
          <span className="display-knob-label">Label opacity</span>
          <input
            type="range"
            min={0}
            max={100}
            value={labelOpacity}
            onChange={(e) => onLabelOpacity(Number(e.target.value))}
          />
          <CommitNumberInput
            value={labelOpacity}
            min={0}
            max={100}
            suffix="%"
            title="Label opacity percent"
            onCommit={(n) => onLabelOpacity(clampPct(n))}
          />
        </label>
      )}
      {regionOpacity != null && onRegionOpacity && (
        <label className="display-knob" title="Immutable region-mask overlay opacity">
          <span className="display-knob-label">Region opacity</span>
          <input
            type="range"
            min={0}
            max={100}
            value={regionOpacity}
            onChange={(e) => onRegionOpacity(Number(e.target.value))}
          />
          <CommitNumberInput
            value={regionOpacity}
            min={0}
            max={100}
            suffix="%"
            title="Region-mask opacity percent"
            onCommit={(n) => onRegionOpacity(clampPct(n))}
          />
        </label>
      )}
      {roiOnly != null && onRoiOnly && (
        <label className="display-knob roi-only-control" title="Show only the mitochondria that touch the region mask — each one whole — and protect voxels outside it from edits">
          <input
            type="checkbox"
            checked={roiOnly}
            onChange={(event) => onRoiOnly(event.target.checked)}
          />
          <span className="display-knob-label">Only inside region mask</span>
        </label>
      )}
      {trailing != null && <div className="display-knobs-trailing">{trailing}</div>}
    </div>
  );
}
