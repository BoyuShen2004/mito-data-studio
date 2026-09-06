import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { AnnotationTask } from "../types/task";
import {
  AnnotationTimeCell,
  DatasetVolumesTable,
  MetadataDetailsCard,
  VolumeMetaBlock,
} from "./VolumeMeta";

describe("shared volume metadata", () => {
  it("uses the same complete field shape and preserves missing voxel size", () => {
    const volume = { name: "very-long-volume-name", file_format: "hdf5", shape_z: 4, shape_y: 8, shape_x: 10, label_type: "prediction", has_region_mask: true, region_mask_location: "regions/sample-mask.h5", region_mask_coverage: 0.25, region_streaming_status: "ready" as const };
    render(<><VolumeMetaBlock volume={volume} scientificMetadata={{}}/><DatasetVolumesTable volumes={[volume]}/></>);
    expect(screen.getAllByText("4 × 8 × 10")).toHaveLength(2);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.getAllByText("prediction")).toHaveLength(2);
    expect(screen.getAllByText("25%")).toHaveLength(2);
    expect(screen.getAllByTitle("very-long-volume-name")).toHaveLength(2);
    const tableName = screen.getAllByTitle("very-long-volume-name")[1];
    expect(tableName.classList.contains("volume-name-cell")).toBe(true);
    expect(tableName.classList.contains("truncate")).toBe(false);
    // Path lives under Data layers (Region · …), not a standalone Metadata row.
    expect(screen.queryByText("Region mask")).toBeNull();
    expect(screen.queryByText("regions/sample-mask.h5")).toBeNull();
    expect(screen.queryByText("Has region mask")).toBeNull();
    expect(screen.queryByText("Region streaming")).toBeNull();
    expect(screen.queryByText("Volume metadata")).toBeNull();
    for (const label of [
      "Organism / species", "Tissue or organ", "Cell type", "Imaging modality",
      "Imaging instrument / microscope", "Experimental condition", "Sample condition",
      "Dataset source", "Publication / reference", "Notes",
    ]) expect(screen.getByText(label)).toBeTruthy();
    expect(screen.getAllByText("—")).toHaveLength(12);
    const labels = Array.from(document.querySelectorAll("dt")).map((node) => node.textContent);
    expect(labels.indexOf("Region coverage")).toBe(labels.indexOf("Label type") + 1);
  });

  it("keeps coverage explicit but omits mask-only detail without a mask", () => {
    render(<VolumeMetaBlock volume={{ name: "no-roi", has_region_mask: false }}/>);
    expect(screen.getByText("Region coverage").nextElementSibling?.textContent).toBe("—");
    expect(screen.queryByText("Region mask")).toBeNull();
    expect(screen.queryByText("Region streaming")).toBeNull();
  });

  it("does not invent empty Details or Streaming columns", () => {
    render(<DatasetVolumesTable volumes={[{ name: "shared-crop" }]}/>);
    expect(screen.queryByRole("columnheader", { name: "Streaming" })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Details" })).toBeNull();
    expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
      "Volume", "Format", "Shape (Z × Y × X)", "Voxel size (Z × Y × X)",
      "Region coverage", "Label type",
    ]);
  });

  it("customizes and centers the action column only when requested", () => {
    const {rerender} = render(<DatasetVolumesTable volumes={[{name: "shared-crop"}]} action={() => <button>Open</button>}/>);
    expect(screen.getByRole("columnheader", {name: "Actions"}).className).toContain("action-align-start");

    rerender(<DatasetVolumesTable
      volumes={[{name: "shared-crop"}]}
      action={() => <button>View</button>}
      actionLabel="View"
      actionAlign="center"
    />);
    expect(screen.getByRole("columnheader", {name: "View"}).className).toContain("action-align-center");
    expect(screen.getByRole("button", {name: "View"}).closest("td")?.className).toContain("action-align-center");
  });
});

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

const volume = {...task, name: "volume"};

describe("MetadataDetailsCard", () => {
  it("shows Raw, Region, Labels in order inside the single Metadata card", () => {
    render(<MemoryRouter><MetadataDetailsCard volume={volume} task={task}/></MemoryRouter>);
    expect(screen.getAllByRole("heading", {name: "Metadata"})).toHaveLength(1);
    expect(screen.queryByText("Volume metadata")).toBeNull();
    expect(screen.queryByText("Volume (source)")).toBeNull();
    expect(screen.queryByText("Chunk / crop")).toBeNull();
    const cell = screen.getByText(/Raw · image\.tif/).closest("td")!;
    expect(within(cell).getAllByText(/Raw|Region|Labels/).map((row) => row.textContent)).toEqual([
      "Raw · image.tif", "Region · region.tif", "Labels · mask.tif prediction",
    ]);
  });

  it("never carries task fields — those belong to the task page's sidebar", () => {
    render(<MemoryRouter><MetadataDetailsCard volume={volume} task={task}/></MemoryRouter>);
    const metadata = screen.getByRole("heading", {name: "Metadata"}).closest("section")!;
    for (const label of ["Assignee", "Priority", "Difficulty", "Deadline", "Instructions"]) {
      expect(within(metadata).queryByText(label)).toBeNull();
    }
    expect(document.querySelectorAll(".details-metadata-card")).toHaveLength(1);
  });

  it("renders a volume that has no task yet — the artifact exists first", () => {
    render(<MemoryRouter><MetadataDetailsCard volume={volume}/></MemoryRouter>);
    expect(screen.getByRole("heading", {name: "Metadata"})).toBeTruthy();
    expect(screen.getByText("Project #2")).toBeTruthy();
    expect(screen.getByText(/Raw · image\.tif/)).toBeTruthy();
  });
});

/** `unmeasured is never zero` — see docs/product-invariants.md. */
describe("AnnotationTimeCell", () => {
  it("shows the measured total with the precise value in a tooltip", () => {
    render(<AnnotationTimeCell time={{tracked: true, seconds: 8040, display: "2h 14m"}}/>);
    const cell = screen.getByText("2h 14m");
    expect(cell.className).not.toContain("annotation-time-unknown");
    expect(cell.getAttribute("title")).toMatch(/Measured annotation time/);
  });

  it("shows — for a legacy-exempt task, not a fabricated zero", () => {
    render(<AnnotationTimeCell time={{tracked: false, seconds: null, display: "—"}}/>);
    const cell = screen.getByText("—", {selector: ".annotation-time-unknown"});
    expect(cell.className).toContain("annotation-time-unknown");
    expect(cell.getAttribute("title")).toMatch(/before time tracking/);
    expect(screen.queryByText("0m")).toBeNull();
  });

  it("shows 0m for an eligible task nobody has opened yet", () => {
    render(<AnnotationTimeCell time={{tracked: true, seconds: 0, display: "0m"}}/>);
    const cell = screen.getByText("0m");
    expect(cell.className).not.toContain("annotation-time-unknown");
    expect(cell.getAttribute("title")).toMatch(/Measured annotation time/);
  });

  it("degrades to the honest unknown when the server sent no time at all", () => {
    render(<AnnotationTimeCell/>);
    expect(screen.getByText("—", {selector: ".annotation-time-unknown"})).toBeTruthy();
  });
});
