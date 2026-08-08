import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TaskSharePage from "./TaskSharePage";

const canvasProps = vi.hoisted(() => vi.fn());

vi.mock("../hooks/useAsync", () => ({
  useAsync: () => ({
    loading: false,
    error: null,
    data: {
      task_id: 7,
      volume_id: 9,
      z_start: 3,
      z_end: 8,
      volume_name: "volume-a",
      project_title: "project-a",
      shape: { z: 12, y: 32, x: 32 },
      dtype: "uint8",
      axes: ["z", "y", "x"],
      has_label: true,
      has_region_mask: true,
      display_range: { lo: 0, hi: 255 },
    },
  }),
}));

vi.mock("../features/viewer/AnnotationCanvas", () => ({
  default: (props: unknown) => {
    canvasProps(props);
    return <div data-testid="annotation-canvas" />;
  },
}));

describe("TaskSharePage", () => {
  beforeEach(() => canvasProps.mockClear());

  it("mounts the shared task through the read-only canvas", () => {
    render(
      <MemoryRouter initialEntries={["/share/task/token-1"]}>
        <Routes>
          <Route path="/share/task/:token" element={<TaskSharePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("READ-ONLY · NO ACCOUNT NEEDED")).toBeTruthy();
    expect(screen.getByTestId("annotation-canvas")).toBeTruthy();
    expect(canvasProps).toHaveBeenLastCalledWith(
      expect.objectContaining({
        taskId: 7,
        volumeId: 9,
        zStart: 3,
        zEnd: 8,
        editable: false,
      }),
    );
  });
});
