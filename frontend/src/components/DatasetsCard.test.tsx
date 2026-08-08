import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import DatasetsCard from "./DatasetsCard";
import type { Dataset } from "../api/datasets";
import type { Volume } from "../types/volume";

const auth = vi.hoisted(() => ({isManager: true, isRequester: false}));
vi.mock("../auth/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("../api/datasets", () => ({
  datasetDependents: vi.fn(),
  deleteDataset: vi.fn(),
  updateDataset: vi.fn(),
}));
vi.mock("./DeleteButton", () => ({ default: () => <button>Delete</button> }));

const dataset = { id: 1, name: "nag_p10_batch1", description: "", metadata: {}, image_directory: "", region_mask_directory: "", mask_directory: "" } as unknown as Dataset;

const volume = (over: Partial<Volume> = {}): Volume => ({
  id: 7,
  dataset: 1,
  name: "nag_p10_c01",
  file_format: "hdf5",
  has_region_mask: true,
  streaming_status: "ready",
  region_streaming_status: "building",
  ...over,
}) as unknown as Volume;

const renderCard = (volumes: Volume[]) =>
  render(
    <MemoryRouter>
      <DatasetsCard datasets={[dataset]} volumes={volumes} projectId={4} onChanged={vi.fn()} />
    </MemoryRouter>,
  );

describe("manager Data volume table", () => {
  it("puts readiness in Streaming and the volume route under Details", () => {
    renderCard([volume()]);
    expect(screen.getByRole("columnheader", { name: "Streaming" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Details" })).toBeTruthy();
    expect(screen.getByText("Image ready")).toBeTruthy();
    expect(screen.getByText("Region building…")).toBeTruthy();
    expect(screen.getByRole("link", {name: "Details"}).getAttribute("href")).toBe("/volumes/7");
    expect(screen.queryByRole("columnheader", {name: "View / Annotate"})).toBeNull();
  });

  it("has no per-volume Tasks column — a volume is one assignable unit", () => {
    renderCard([volume(), volume({ id: 8, name: "nag_p10_c02" })]);
    expect(screen.queryByRole("columnheader", { name: "Tasks" })).toBeNull();
    expect(screen.queryByText(/\d+ tasks?$/)).toBeNull();
    expect(screen.queryByText(/undefined/)).toBeNull();
  });

  it("keeps the dataset inventory line, which is not a restated badge", () => {
    renderCard([volume(), volume({ id: 8, name: "nag_p10_c02" })]);
    expect(screen.getByText("· 2 volume pairs")).toBeTruthy();
  });

  it("shows only the image badge when a volume has no Region to stream", () => {
    renderCard([volume({ has_region_mask: false, region_streaming_status: "absent" })]);
    expect(screen.getByText("Image ready")).toBeTruthy();
    expect(screen.queryByText(/^Region (ready|building|failed|not built)/)).toBeNull();
  });

  it("does not expose manager/requester inventory columns to an annotator", () => {
    auth.isManager = false;
    auth.isRequester = false;
    renderCard([volume()]);
    expect(screen.queryByRole("columnheader", {name: "Streaming"})).toBeNull();
    expect(screen.getByRole("columnheader", {name: "Details"})).toBeTruthy();
    auth.isManager = true;
  });
});
