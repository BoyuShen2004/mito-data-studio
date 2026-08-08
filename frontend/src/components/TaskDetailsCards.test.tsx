import {render, screen, within} from "@testing-library/react";
import {MemoryRouter} from "react-router-dom";
import {describe, expect, it} from "vitest";
import type {AnnotationTask} from "../types/task";
import {MetadataDetailsCard, TaskDetailsStack} from "./TaskDetailsCards";

const task = {
  id: 40,
  project: 2,
  project_title: "Project P",
  dataset: "Dataset D",
  dataset_metadata: {},
  volume_name: "volume",
  image_location: "/raw/image.tif",
  region_mask_location: "/roi/region.tif",
  label_location: "/labels/mask.tif",
  has_region_mask: true,
  label_type: "prediction",
  volume_status: "registered",
  status: "assigned",
  task_type: "manual_annotation",
  review_history: [],
  can_submit: true,
} as unknown as AnnotationTask;

const headings = () =>
  screen.getAllByRole("heading").map((node) => node.textContent?.trim());

describe("shared task Details cards", () => {
  it("shows Raw, Region, Labels in order inside the single Metadata card", () => {
    render(<MemoryRouter><MetadataDetailsCard volume={{...task, name: "volume"}} task={task}/></MemoryRouter>);
    expect(screen.getAllByRole("heading", {name: "Metadata"})).toHaveLength(1);
    expect(screen.queryByText("Volume metadata")).toBeNull();
    expect(screen.queryByText("Volume (source)")).toBeNull();
    expect(screen.queryByText("Chunk / crop")).toBeNull();
    const cell = screen.getByText(/Raw · image\.tif/).closest("td")!;
    expect(within(cell).getAllByText(/Raw|Region|Labels/).map((row) => row.textContent)).toEqual([
      "Raw · image.tif", "Region · region.tif", "Labels · mask.tif prediction",
    ]);
  });

  it("keeps Metadata, Task # and Offline upload three distinct cards", () => {
    render(<MemoryRouter><TaskDetailsStack
      volume={{...task, name: "volume"}}
      tasks={[task]}
      primaryTask={task}
      streamingCard={<section className="card"><h3>Streaming</h3></section>}
    /></MemoryRouter>);

    expect(headings()).toEqual([
      "Streaming", "Metadata", "Task #40 assigned", "Offline annotation upload",
    ]);
    // The merge this regressed to before: task fields inside the Metadata card.
    const metadata = screen.getByRole("heading", {name: "Metadata"}).closest("section")!;
    for (const label of ["Assignee", "Priority", "Difficulty", "Deadline", "Instructions"]) {
      expect(within(metadata).queryByText(label)).toBeNull();
    }
    expect(document.querySelectorAll(".details-metadata-card")).toHaveLength(1);
    expect(document.querySelectorAll(".details-task-card")).toHaveLength(1);
    expect(document.querySelectorAll(".details-offline-upload-card")).toHaveLength(1);
  });

  it("swaps only the Metadata card when the manager edits it", () => {
    render(<MemoryRouter><TaskDetailsStack
      volume={{...task, name: "volume"}}
      tasks={[task]}
      primaryTask={task}
      metadataCard={<section className="card"><h3>Metadata</h3><form/></section>}
    /></MemoryRouter>);

    expect(headings()).toEqual([
      "Metadata", "Task #40 assigned", "Offline annotation upload",
    ]);
    expect(document.querySelectorAll(".details-metadata-card")).toHaveLength(0);
    expect(document.querySelectorAll(".details-task-card")).toHaveLength(1);
    expect(document.querySelectorAll(".details-offline-upload-card")).toHaveLength(1);
  });
});
