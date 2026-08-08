import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import JumpToRegionButton from "./JumpToRegionButton";
import { clearRegionIndexCache } from "./regionIndex";
import type { Axis, RegionIndex } from "../../api/viewer";

const answer = (indices: number[], axis: Axis = "z"): RegionIndex => ({
  axis,
  length: indices.length,
  indices,
});

function mount(props: Partial<Parameters<typeof JumpToRegionButton>[0]> = {}) {
  const onJump = vi.fn();
  const getRegionIndex = vi.fn(async () => answer([10, 11, 12]));
  const view = render(
    <JumpToRegionButton
      volumeId={7}
      axis="z"
      index={30}
      hasRegion
      getRegionIndex={getRegionIndex}
      onJump={onJump}
      {...props}
    />,
  );
  return { onJump, getRegionIndex, view };
}

describe("JumpToRegionButton", () => {
  beforeEach(() => {
    clearRegionIndexCache();
    window.sessionStorage.clear();
  });

  it("is not rendered at all for a volume without a region mask", () => {
    mount({ hasRegion: false });
    expect(screen.queryByRole("button", { name: /jump to region/i })).toBeNull();
  });

  it("prefetches in the background when mounted", async () => {
    const { getRegionIndex } = mount();
    expect(screen.getByRole("button", { name: /jump to region/i })).toBeTruthy();
    await waitFor(() => expect(getRegionIndex).toHaveBeenCalledWith(7, "z"));
  });

  it("jumps to the nearest slice that has region", async () => {
    const { onJump } = mount();

    await userEvent.click(screen.getByRole("button", { name: /jump to region/i }));

    await waitFor(() => expect(onJump).toHaveBeenCalledWith(12));
  });

  it("does nothing when this slice already has region", async () => {
    const { onJump } = mount({ index: 11 });

    await userEvent.click(screen.getByRole("button", { name: /jump to region/i }));

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /jump to region/i }).getAttribute("title"),
      ).toMatch(/already has region/i),
    );
    expect(onJump).not.toHaveBeenCalled();
  });

  it("reports a volume whose region is empty instead of jumping", async () => {
    const { onJump } = mount({ getRegionIndex: vi.fn(async () => answer([])) });

    await userEvent.click(screen.getByRole("button", { name: /jump to region/i }));

    const button = await screen.findByRole("button", { name: /jump to region/i });
    await waitFor(() => expect(button.getAttribute("title")).toMatch(/no layer/i));
    expect(button).toHaveProperty("disabled", true);
    expect(onJump).not.toHaveBeenCalled();
  });

  it("surfaces a failure and stays clickable", async () => {
    const getRegionIndex = vi
      .fn<() => Promise<RegionIndex>>()
      .mockRejectedValueOnce(new Error("region layer unreadable"))
      .mockRejectedValueOnce(new Error("region layer unreadable"))
      .mockResolvedValueOnce(answer([10]));
    const { onJump } = mount({ getRegionIndex });

    const button = screen.getByRole("button", { name: /jump to region/i });
    await userEvent.click(button);
    await waitFor(() =>
      expect(button.getAttribute("title")).toMatch(/region layer unreadable/i),
    );

    await userEvent.click(button);
    await waitFor(() => expect(onJump).toHaveBeenCalledWith(10));
  });
});
