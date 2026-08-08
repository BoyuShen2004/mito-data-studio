import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DisplayKnobs from "./DisplayKnobs";

const base = {
  brightness: 50,
  contrast: 50,
  onBrightness: vi.fn(),
  onContrast: vi.fn(),
};

describe("DisplayKnobs ROI-only control", () => {
  it("is absent when the viewer has no region mask", () => {
    render(<DisplayKnobs {...base} />);
    expect(screen.queryByText("Only inside region mask")).toBeNull();
  });

  it("toggles with one click when a region mask exists", () => {
    const onRoiOnly = vi.fn();
    render(
      <DisplayKnobs
        {...base}
        regionOpacity={45}
        onRegionOpacity={vi.fn()}
        roiOnly={false}
        onRoiOnly={onRoiOnly}
      />,
    );
    fireEvent.click(screen.getByLabelText("Only inside region mask"));
    expect(onRoiOnly).toHaveBeenCalledWith(true);
  });
});
