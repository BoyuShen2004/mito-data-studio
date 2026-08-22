import {render, screen, within} from "@testing-library/react";
import {MemoryRouter} from "react-router-dom";
import {describe, expect, it} from "vitest";
import type {AnnotationTask} from "../types/task";
import {MetadataDetailsCard, TaskDetailsCard, TaskDetailsStack} from "./TaskDetailsCards";

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

/** The row order Details promises: Time is the row directly below Instructions. */
const rowLabels = (card: HTMLElement) =>
  Array.from(card.querySelectorAll("tbody th")).map((th) => th.textContent);

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


describe("annotation time on the task Details card", () => {
  const timed = {
    ...task,
    instructions: "Trace every mitochondrion.",
    annotation_time: {tracked: true, seconds: 8040, display: "2h 14m"},
  } as unknown as AnnotationTask;

  it("shows cumulative time on one row directly below Instructions", () => {
    const {container} = render(<MemoryRouter><TaskDetailsCard task={timed}/></MemoryRouter>);
    const card = container.querySelector(".details-task-card") as HTMLElement;
    const labels = rowLabels(card);
    expect(labels).toContain("Time");
    expect(labels.indexOf("Time")).toBe(labels.indexOf("Instructions") + 1);
    expect(labels[labels.length - 1]).toBe("Time");
    expect(screen.getByText("2h 14m")).toBeTruthy();
  });

  it("shows `-` for a legacy-exempt task, not a fabricated zero", () => {
    const legacy = {
      ...task,
      annotation_time: {tracked: false, seconds: null, display: "-"},
    } as unknown as AnnotationTask;
    render(<MemoryRouter><TaskDetailsCard task={legacy}/></MemoryRouter>);
    const cell = screen.getByText("-");
    expect(cell.className).toContain("annotation-time-unknown");
    expect(cell.getAttribute("title")).toMatch(/before time tracking/);
    expect(screen.queryByText("0m")).toBeNull();
  });

  it("shows 0m for an eligible task nobody has opened yet", () => {
    const fresh = {
      ...task,
      annotation_time: {tracked: true, seconds: 0, display: "0m"},
    } as unknown as AnnotationTask;
    render(<MemoryRouter><TaskDetailsCard task={fresh}/></MemoryRouter>);
    const cell = screen.getByText("0m");
    expect(cell.className).not.toContain("annotation-time-unknown");
    expect(cell.getAttribute("title")).toMatch(/Measured annotation time/);
  });

  it("degrades to the honest unknown when the server sent no time at all", () => {
    const missing = {...task} as unknown as AnnotationTask;
    render(<MemoryRouter><TaskDetailsCard task={missing}/></MemoryRouter>);
    expect(screen.getByText("-")).toBeTruthy();
  });

  it("does not let the timer dominate the card", () => {
    const {container} = render(<MemoryRouter><TaskDetailsCard task={timed}/></MemoryRouter>);
    const card = container.querySelector(".details-task-card") as HTMLElement;
    // Exactly one row, inside the same table as every other task fact — no
    // heading, no panel, no separate card of its own.
    expect(card.querySelectorAll(".annotation-time")).toHaveLength(1);
    expect(within(card).queryByRole("heading", {name: /time/i})).toBeNull();
  });
});
