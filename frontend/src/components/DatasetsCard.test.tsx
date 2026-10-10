import { fireEvent, render, screen, within } from "@testing-library/react";
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

const renderCard = (volumes: Volume[]) => {
  const view = render(
    <MemoryRouter>
      <DatasetsCard datasets={[dataset]} volumes={volumes} projectId={4} onChanged={vi.fn()} />
    </MemoryRouter>,
  );
  return view;
};

describe("manager Data volume table", () => {
  it("shows a single dataset's volumes directly", () => {
    renderCard([volume()]);
    expect(screen.queryByRole("button", { name: dataset.name })).toBeNull();
    expect(screen.getByRole("table")).toBeTruthy();
  });
  it("keeps independent dataset drafts when another dataset editor opens", () => {
    render(<MemoryRouter><DatasetsCard datasets={[dataset, { ...dataset, id: 2, name: "second_dataset" }]}
      volumes={[]} projectId={4} onChanged={vi.fn()} /></MemoryRouter>);
    const first = screen.getByRole("button", { name: dataset.name });
    const second = screen.getByRole("button", { name: "second_dataset" });
    fireEvent.click(first);
    const firstSection = within(first.closest("section")!);
    fireEvent.click(firstSection.getByRole("button", { name: "Edit" }));
    fireEvent.change(firstSection.getByLabelText("Dataset name"), { target: { value: "First draft" } });
    fireEvent.click(first);
    fireEvent.click(second);
    const secondSection = within(second.closest("section")!);
    fireEvent.click(secondSection.getByRole("button", { name: "Edit" }));
    fireEvent.change(secondSection.getByLabelText("Dataset name"), { target: { value: "Second draft" } });
    fireEvent.click(first);
    expect((firstSection.getByLabelText("Dataset name") as HTMLInputElement).value).toBe("First draft");
    expect((secondSection.getByLabelText("Dataset name") as HTMLInputElement).value).toBe("Second draft");
  });
  it("shows only the inventory until expansion and retains an editing draft on collapse", () => {
    render(<MemoryRouter><DatasetsCard datasets={[dataset, { ...dataset, id: 2, name: "second_dataset" }]} volumes={[volume()]} projectId={4} onChanged={vi.fn()} /></MemoryRouter>);
    const toggle = screen.getByRole("button", { name: dataset.name });
    expect(screen.queryByRole("table")).toBeNull();
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Dataset name"), { target: { value: "Unsaved dataset name" } });
    fireEvent.click(toggle);
    expect(screen.queryByRole("textbox")).toBeNull();
    fireEvent.click(toggle);
    expect((screen.getByLabelText("Dataset name") as HTMLInputElement).value).toBe("Unsaved dataset name");
  });
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
