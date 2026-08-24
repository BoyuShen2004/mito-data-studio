import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { AxisControls } from "./AnnotationCanvas";
import RegionOnlyButton from "./RegionOnlyButton";

function controls(canMutateLabels: boolean): AxisControls {
  return {
    axis: "z",
    changeAxis: vi.fn(),
    disabled: false,
    currentLocation: () => ({ z: 0, y: 0, x: 0, axis: "z" }),
    hasRegion: true,
    regionOnly: false,
    changeRegionOnly: vi.fn(),
    canMutateLabels,
    regionOverwriteMode: "overwrite_empty",
    changeRegionOverwriteMode: vi.fn(),
  };
}

it("keeps Region only in View but hides the edit-only Overwrite policy", () => {
  const viewControls = controls(false);
  render(<RegionOnlyButton controls={viewControls} />);
  fireEvent.click(screen.getByRole("button", { name: "Region only" }));
  expect(viewControls.changeRegionOnly).toHaveBeenCalledWith(true);
  expect(screen.queryByLabelText("Overwrite")).toBeNull();
});

it("shows Overwrite on an editable Annotate surface", () => {
  render(<RegionOnlyButton controls={controls(true)} />);
  expect(screen.getByLabelText("Overwrite")).toBeTruthy();
});
