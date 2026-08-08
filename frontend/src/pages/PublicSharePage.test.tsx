import { fireEvent, render, screen, within } from "@testing-library/react";
import { useEffect } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PublicShareBrowse } from "../api/shares";
import PublicSharePage from "./PublicSharePage";

const api = vi.hoisted(() => ({getPublicShare: vi.fn()}));
vi.mock("../api/shares", () => api);

const canvasProps = vi.hoisted(() => vi.fn());
/** What the stub canvas publishes to `onAxisControls`; a test sets `hasRegion`
 * before rendering to stand in for the volume meta's `has_region_mask`. */
const publishedControls = vi.hoisted(() => ({hasRegion: false}));
vi.mock("../features/viewer/AnnotationCanvas", () => ({
  default: (props: Record<string, unknown>) => {
    canvasProps(props);
    const publish = props.onAxisControls as
      | ((c: Record<string, unknown> | null) => void)
      | undefined;
    // The real canvas publishes its axis handle from an effect; mirroring that
    // here is what exercises the page's topbar wiring rather than asserting the
    // prop was merely passed.
    useEffect(() => {
      publish?.({
        axis: "z",
        changeAxis: axisChanged,
        disabled: false,
        currentLocation: () => ({}),
        hasRegion: publishedControls.hasRegion,
        regionOnly: false,
        changeRegionOnly: regionOnlyChanged,
      });
      return () => publish?.(null);
    }, [publish]);
    return <div data-testid="annotation-canvas"/>;
  },
}));
const axisChanged = vi.hoisted(() => vi.fn());
const regionOnlyChanged = vi.hoisted(() => vi.fn());

const base = {
  id: 1, token: "tok", project_id: 5, project_title: "Cortex EM",
  created_at: "2026-08-04T00:00:00Z", revoked_at: null, url: "/share/public/tok",
  created_by: 2, created_by_username: "manager-a",
  dataset_id: null as number | null, dataset_name: "", volume_id: null as number | null, volume_name: "",
};
const datasets = [
  {id: 11, name: "Dataset A", description: "Cortex crops"},
  {id: 12, name: "Dataset B", description: ""},
];
const volumes = [
  {id: 101, dataset_id: 11, name: "crop-a", shape: [12, 256, 256], voxel_size: [0.04, 0.008, 0.008], file_format: "hdf5", label_type: "prediction"},
  {id: 102, dataset_id: 12, name: "crop-b", shape: [8, 128, 128], voxel_size: [null, null, null], file_format: "zarr", label_type: "none"},
];
const projectShare: PublicShareBrowse = {...base, scope: "project", datasets, volumes};
const datasetShare: PublicShareBrowse = {
  ...base, scope: "dataset", dataset_id: 11, dataset_name: "Dataset A",
  datasets: [datasets[0]], volumes: [volumes[0]],
};
const volumeShare: PublicShareBrowse = {
  ...base, scope: "volume", dataset_id: 11, dataset_name: "Dataset A",
  volume_id: 101, volume_name: "crop-a", datasets: [datasets[0]], volumes: [volumes[0]],
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/share/public/tok"]}>
      <Routes>
        <Route path="/share/public/:token" element={<PublicSharePage/>}/>
      </Routes>
    </MemoryRouter>,
  );
}

/** The action button in the table row that names `label`. */
function rowAction(label: string, action: string) {
  const row = screen.getByText(label).closest("tr, [data-share-row]");
  expect(row).toBeTruthy();
  return within(row as HTMLElement).getByRole("button", {name: action});
}

describe("PublicSharePage", () => {
  beforeEach(() => {
    canvasProps.mockClear();
    axisChanged.mockClear();
    regionOnlyChanged.mockClear();
    publishedControls.hasRegion = false;
    api.getPublicShare.mockReset().mockResolvedValue(projectShare);
  });

  it("drills a project share from datasets to a volume table to the viewer", async () => {
    renderPage();

    // Dataset index first — volumes are not dumped on the landing view.
    expect(await screen.findByRole("heading", {name: "Datasets"})).toBeTruthy();
    expect(screen.getByText("Project share")).toBeTruthy();
    expect(screen.queryByText("crop-a")).toBeNull();
    expect(screen.getByText("No description")).toBeTruthy();
    expect(screen.getAllByLabelText("1 volume")).toHaveLength(2);

    fireEvent.click(rowAction("Dataset A", "Open"));

    // Only that dataset's volumes, as table rows with format + shape.
    expect(screen.getByRole("heading", {name: "Volumes"})).toBeTruthy();
    expect(screen.getByText("crop-a")).toBeTruthy();
    expect(screen.queryByText("crop-b")).toBeNull();
    expect(screen.getByText("hdf5")).toBeTruthy();
    expect(screen.getByText("12 × 256 × 256")).toBeTruthy();
    const viewHeader = screen.getByRole("columnheader", {name: "View"});
    expect(viewHeader.className).toContain("action-align-center");
    expect(screen.queryByRole("columnheader", {name: "View / Annotate"})).toBeNull();
    expect(rowAction("crop-a", "View").closest("td")?.className).toContain("action-align-center");

    fireEvent.click(rowAction("crop-a", "View"));

    expect(screen.getByTestId("annotation-canvas")).toBeTruthy();
    expect(canvasProps).toHaveBeenLastCalledWith(
      expect.objectContaining({volumeId: 101, editable: false, zStart: 0, zEnd: 11}),
    );
    expect(screen.getByText("READ-ONLY · NO ACCOUNT NEEDED")).toBeTruthy();

    // Back lands on the dataset we came from, not the project index.
    fireEvent.click(screen.getByRole("button", {name: "← Browse"}));
    expect(screen.getByText("crop-a")).toBeTruthy();
    expect(screen.queryByText("crop-b")).toBeNull();

    // Breadcrumb climbs back to the dataset index.
    fireEvent.click(screen.getByRole("button", {name: "Cortex EM"}));
    expect(screen.getByRole("heading", {name: "Datasets"})).toBeTruthy();
  });

  it("shows a dataset share's volumes without a dataset index", async () => {
    api.getPublicShare.mockResolvedValue(datasetShare);
    renderPage();

    expect(await screen.findByRole("heading", {name: "Volumes"})).toBeTruthy();
    expect(screen.queryByRole("heading", {name: "Datasets"})).toBeNull();
    expect(screen.getByText("Dataset share")).toBeTruthy();

    fireEvent.click(rowAction("crop-a", "View"));
    expect(canvasProps).toHaveBeenLastCalledWith(
      expect.objectContaining({volumeId: 101, editable: false}),
    );
  });

  it("opens a volume share straight into the read-only viewer", async () => {
    api.getPublicShare.mockResolvedValue(volumeShare);
    renderPage();

    expect(await screen.findByTestId("annotation-canvas")).toBeTruthy();
    expect(canvasProps).toHaveBeenLastCalledWith(
      expect.objectContaining({volumeId: 101, editable: false}),
    );
    // Nothing to browse from a single-volume share.
    expect(screen.queryByRole("button", {name: "← Browse"})).toBeNull();
  });

  it("filters volumes once the list is long", async () => {
    const many = Array.from({length: 12}, (_, index) => ({
      id: 200 + index, dataset_id: 11, name: `crop-${index}`, shape: [4, 64, 64], file_format: "hdf5",
    }));
    api.getPublicShare.mockResolvedValue({...datasetShare, volumes: many});
    renderPage();

    const search = await screen.findByLabelText("Search volumes");
    fireEvent.change(search, {target: {value: "crop-11"}});
    expect(screen.getByText("crop-11")).toBeTruthy();
    expect(screen.queryByText("crop-10")).toBeNull();
  });

  it("gives the read-only viewer the same Axis control as the authenticated View", async () => {
    api.getPublicShare.mockResolvedValue(volumeShare);
    renderPage();

    expect(await screen.findByTestId("annotation-canvas")).toBeTruthy();
    // Wired, not merely accepted: the page must hand the canvas a callback.
    expect(canvasProps).toHaveBeenLastCalledWith(
      expect.objectContaining({editable: false, onAxisControls: expect.any(Function)}),
    );

    const axis = screen.getByLabelText("Axis") as HTMLSelectElement;
    expect(axis.value).toBe("z");
    fireEvent.change(axis, {target: {value: "y"}});
    expect(axisChanged).toHaveBeenCalledWith("y");

    // Read-only stays read-only.
    expect(screen.queryByRole("button", {name: "Annotate"})).toBeNull();
    expect(screen.getByText("READ-ONLY · NO ACCOUNT NEEDED")).toBeTruthy();
  });

  it("shows Region only exactly when the volume has a region mask", async () => {
    api.getPublicShare.mockResolvedValue(volumeShare);
    const withoutRegion = renderPage();
    expect(await screen.findByTestId("annotation-canvas")).toBeTruthy();
    expect(screen.queryByRole("button", {name: "Region only"})).toBeNull();
    withoutRegion.unmount();

    publishedControls.hasRegion = true;
    renderPage();
    expect(await screen.findByTestId("annotation-canvas")).toBeTruthy();
    const button = screen.getByRole("button", {name: "Region only"});
    fireEvent.click(button);
    expect(regionOnlyChanged).toHaveBeenCalledWith(true);
  });

  it("renders browse row actions as high-contrast primary buttons", async () => {
    renderPage();
    expect(await screen.findByRole("heading", {name: "Datasets"})).toBeTruthy();

    // `secondary` is the app's low-emphasis style — near-invisible on a white
    // table row, and wrong for a row whose only affordance this is.
    const open = rowAction("Dataset A", "Open");
    expect(open.className).not.toContain("secondary");

    fireEvent.click(open);
    const view = rowAction("crop-a", "View");
    expect(view.className).not.toContain("secondary");
  });

  it("explains a revoked share instead of rendering an empty page", async () => {
    api.getPublicShare.mockRejectedValue(new Error("The manager closed this share."));
    renderPage();

    expect(await screen.findByRole("heading", {name: "This link isn’t available"})).toBeTruthy();
    expect(screen.getByText(/The manager closed this share/)).toBeTruthy();
  });
});
