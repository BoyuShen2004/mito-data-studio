import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DatasetVolumesTable, VolumeMetaBlock } from "./VolumeMeta";

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
    expect(screen.getAllByText("-")).toHaveLength(10);
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
