import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Volume } from "../types/volume";
import { StreamingStatusCard } from "./VolumeDetailPage";

const volume = (state: Volume["streaming_status"]): Volume => ({
  id: 3,
  ready_streaming: state === "ready",
  streaming_status: state,
  streaming_error: state === "failed" ? "checksum mismatch" : "",
} as Volume);

describe("StreamingStatusCard", () => {
  it("offers a manager retry while keeping fallback status visible", () => {
    const onBuild = vi.fn();
    render(
      <StreamingStatusCard
        volume={volume("failed")}
        isManager
        busy={false}
        notice={null}
        onBuild={onBuild}
      />,
    );
    expect(screen.getByText(/continue through the original source/)).toBeTruthy();
    expect(screen.getByText("checksum mismatch")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry pyramid" }));
    expect(onBuild).toHaveBeenCalledOnce();
  });

  it("does not allow a duplicate build while one is running", () => {
    render(
      <StreamingStatusCard
        volume={volume("building")}
        isManager
        busy={false}
        notice={null}
        onBuild={vi.fn()}
      />,
    );
    expect((screen.getByRole("button", { name: "Build pyramid" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows no region row for a volume that has no ROI", () => {
    render(
      <StreamingStatusCard
        volume={volume("ready")}
        isManager
        busy={null}
        notice={null}
        onBuild={vi.fn()}
      />,
    );
    expect(screen.queryByText("Region mask")).toBeNull();
    expect(screen.getByText("Image")).toBeTruthy();
  });

  it("builds the two layers independently", () => {
    const onBuild = vi.fn();
    render(
      <StreamingStatusCard
        volume={{
          ...volume("ready"),
          has_region_mask: true,
          region_streaming_status: "failed",
          region_streaming_error: "region source unreadable",
        } as Volume}
        isManager
        busy={null}
        notice={null}
        onBuild={onBuild}
      />,
    );
    expect(screen.getByText("region source unreadable")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry region" }));
    expect(onBuild).toHaveBeenCalledWith("region");
    fireEvent.click(screen.getByRole("button", { name: "Rebuild pyramid" }));
    expect(onBuild).toHaveBeenLastCalledWith("image");
  });

  it("marks only the layer being queued as busy", () => {
    render(
      <StreamingStatusCard
        volume={{
          ...volume("ready"),
          has_region_mask: true,
          region_streaming_status: "not_built",
        } as Volume}
        isManager
        busy="region"
        notice={null}
        onBuild={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Queueing…" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Rebuild pyramid" })).toBeTruthy();
  });
});
