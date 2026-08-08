import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import RegionCoverage from "./RegionCoverage";

describe("RegionCoverage", () => {
  it("does not invent a percentage without a mask", () => {
    render(<RegionCoverage hasMask={false} coverage={null} />);
    expect(screen.getByText("—")).not.toBeNull();
  });

  it("makes an empty mask obvious", () => {
    render(<RegionCoverage hasMask coverage={0} />);
    expect(screen.getByText("0% · empty")).not.toBeNull();
  });

  it("formats small and ordinary coverage compactly", () => {
    const { rerender } = render(<RegionCoverage hasMask coverage={0.000001} />);
    expect(screen.getByText("<0.1%")).not.toBeNull();
    rerender(<RegionCoverage hasMask coverage={0.003} />);
    expect(screen.getByText("0.3%")).not.toBeNull();
    rerender(<RegionCoverage hasMask coverage={0.12} />);
    expect(screen.getByText("12%")).not.toBeNull();
  });
});
