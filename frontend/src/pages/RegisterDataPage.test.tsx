import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RegisterDataPage from "./RegisterDataPage";

const harness = vi.hoisted(() => ({
  scan: vi.fn(),
  register: vi.fn(),
  snapshot: vi.fn(),
  reconcile: vi.fn(),
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ isManager: true }),
}));

vi.mock("../hooks/useAsync", () => ({
  useAsync: () => ({
    data: [{ id: 7, title: "Heart project" }],
    loading: false,
    error: null,
    reload: vi.fn(),
  }),
}));

vi.mock("../api/registerData", () => ({
  scanDataSources: harness.scan,
  registerData: harness.register,
  getRegistrationSnapshot: harness.snapshot,
  reconcileRegistration: harness.reconcile,
}));

vi.mock("../api/projects", () => ({ listProjects: vi.fn() }));

const imagePath = "/data/heart/image";
const maskPath = "/data/heart/mask";
const imageName = "2026-02-18_18-03__heart__volume.ome.tif";
const maskName = "2026-02-18_18-03__heart__volume.ome_mask.tif";

const result = (editable = "", region = "") => ({
  image_directory: imagePath,
  region_mask_directory: region,
  mask_directory: editable,
  image_files: [{ name: imageName, path: `${imagePath}/${imageName}`, extension: ".tif", size: 1 }],
  region_mask_files: region
    ? [{ name: maskName, path: `${region}/${maskName}`, extension: ".tif", size: 1 }]
    : [],
  mask_files: editable
    ? [{ name: maskName, path: `${editable}/${maskName}`, extension: ".tif", size: 1 }]
    : [],
  pairs: editable
    ? [{ image: imageName, mask: maskName, case: "heart" }]
    : [],
  region_by_image: region ? { [imageName]: maskName } : {},
  unmatched_images: editable ? [] : [imageName],
  unmatched_region_masks: [],
  unmatched_masks: [],
  extra_channels: [],
  pairing_source: "filename",
  split: "",
  suggestions: {
    images: [{ name: "image", path: imagePath, count: 1, split: "", current: true }],
    masks: [{ name: "mask", path: maskPath, count: 1, split: "", current: false }],
  },
  dataset_metadata: {},
  manifest_path: "",
});

describe("Register Data directory roles", () => {
  beforeEach(() => {
    harness.scan.mockReset().mockImplementation(
      async (_image: string, editable: string, region: string) => result(editable, region),
    );
    harness.register.mockReset();
    harness.snapshot.mockReset().mockResolvedValue(null);
    harness.reconcile.mockReset();
  });

  it("aligns three directory fields and hides suggestion chips", async () => {
    render(
      <MemoryRouter>
        <RegisterDataPage />
      </MemoryRouter>,
    );

    expect(screen.getByLabelText(/Raw image directory/)).not.toBeNull();
    expect(screen.getByLabelText(/Region mask directory/)).not.toBeNull();
    expect(screen.getByLabelText(/Editable labels directory/)).not.toBeNull();

    fireEvent.change(screen.getByLabelText(/Raw image directory/), {
      target: { value: imagePath },
    });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    await screen.findByText(/Scanned volumes/);
    expect(screen.queryByText("Region sets:")).toBeNull();
    expect(screen.queryByText("Editable label sets:")).toBeNull();
    expect(screen.queryByText(/Suggested label folders/)).toBeNull();
    expect(
      screen.queryByText(/Region masks remain immutable/),
    ).toBeNull();
    expect((screen.getByLabelText(/Region mask directory/) as HTMLInputElement).value).toBe(
      "",
    );
  });

  it("lets the user set editable and region paths manually before scan", async () => {
    render(
      <MemoryRouter>
        <RegisterDataPage />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText(/Raw image directory/), {
      target: { value: imagePath },
    });
    fireEvent.change(screen.getByLabelText(/Editable labels directory/), {
      target: { value: maskPath },
    });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    await waitFor(() =>
      expect(harness.scan).toHaveBeenLastCalledWith(imagePath, maskPath, ""),
    );
    expect(
      (screen.getByLabelText(/Editable labels directory/) as HTMLInputElement).value,
    ).toBe(maskPath);
    expect(
      (screen.getByLabelText(/Region mask directory/) as HTMLInputElement).value,
    ).toBe("");
  });

  it("keeps Scanned volumes visible before scan and after directory edits", async () => {
    render(
      <MemoryRouter>
        <RegisterDataPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Scanned volumes")).toBeTruthy();

    fireEvent.change(screen.getByLabelText(/Raw image directory/), {
      target: { value: imagePath },
    });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));
    await screen.findByText(imageName);

    fireEvent.change(screen.getByLabelText(/Raw image directory/), {
      target: { value: `${imagePath}-other` },
    });

    expect(screen.getByText("Scanned volumes")).toBeTruthy();
    expect(screen.queryByText(/Scan again to refresh/)).toBeNull();
    expect(screen.queryByText(/Directories changed/)).toBeNull();
    expect(screen.queryByText(/Some label files did not match/)).toBeNull();
  });

  it("does not show tip copy in the scanned volumes header", async () => {
    render(
      <MemoryRouter>
        <RegisterDataPage />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText(/Raw image directory/), {
      target: { value: imagePath },
    });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));
    await screen.findByText("Scanned volumes");

    expect(screen.queryByText(/with region masks/)).toBeNull();
    expect(screen.queryByText(/Prediction =/)).toBeNull();
    expect(screen.queryByText(/without initial labels/)).toBeNull();
  });
});

// Volumes are renamed per row in the scanned list, so a single global "Volume
// name" could only shadow those names or be ignored. It is gone, and the
// request no longer carries one.
describe("Register Data volume naming", () => {
  beforeEach(() => {
    harness.scan.mockReset().mockImplementation(
      async (_image: string, editable: string, region: string) => result(editable, region),
    );
    harness.register.mockReset().mockResolvedValue({
      project: { id: 7, title: "Heart project" },
      volumes: [{ id: 1 }],
    });
    harness.snapshot.mockReset().mockResolvedValue(null);
    harness.reconcile.mockReset();
  });

  const scanned = async () => {
    render(
      <MemoryRouter>
        <RegisterDataPage />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText(/Raw image directory/), {
      target: { value: imagePath },
    });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));
    await screen.findByText("Scanned volumes");
    fireEvent.change(screen.getByLabelText(/Project \*/), {
      target: { value: "7" },
    });
  };

  it("has no global Volume name field", async () => {
    await scanned();
    expect(screen.queryByLabelText(/Volume name/)).toBeNull();
    expect(screen.getByLabelText(/Dataset name/)).not.toBeNull();
    expect(screen.getByLabelText(/Project \*/)).not.toBeNull();
  });

  it("registers with only a dataset name — no volume name is required", async () => {
    await scanned();
    fireEvent.change(screen.getByLabelText(/Dataset name/), {
      target: { value: "Heart set" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Register 1 dataset/ }));

    await waitFor(() => expect(harness.register).toHaveBeenCalledTimes(1));
    const payload = harness.register.mock.calls[0][0];
    expect(payload.dataset).toBe("Heart set");
    expect(payload.volume).toBeUndefined();
  });

  it("sends the per-row rename as that volume's name", async () => {
    await scanned();
    fireEvent.change(screen.getByLabelText(/Dataset name/), {
      target: { value: "Heart set" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Edit$/ }));
    fireEvent.change(screen.getByLabelText(/Rename volume/), {
      target: { value: "crop-A" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Register 1 dataset/ }));

    await waitFor(() => expect(harness.register).toHaveBeenCalledTimes(1));
    expect(harness.register.mock.calls[0][0].pairs).toEqual([
      { image: imageName, region_mask: undefined, mask: undefined, name: "crop-A" },
    ]);
  });

  it("keeps rename inputs collapsed until Edit is pressed", async () => {
    await scanned();
    expect(screen.queryByLabelText(/Rename volume/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Edit$/ }));
    expect(screen.getByLabelText(/Rename volume/)).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Done$/ }));
    expect(screen.queryByLabelText(/Rename volume/)).toBeNull();
  });

  it("keeps the queued-directory table free of a source-volume column", async () => {
    await scanned();
    fireEvent.change(screen.getByLabelText(/Dataset name/), {
      target: { value: "Heart set" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /Add another directory/ }),
    );
    await screen.findByText(/Queued to register/);
    expect(screen.queryByRole("columnheader", { name: "Source volume" })).toBeNull();
    expect(screen.getByRole("columnheader", { name: "Dataset" })).not.toBeNull();
  });
});

describe("Register Data exclusive label pairing", () => {
  const imgA = "vol_a_im.h5";
  const imgB = "vol_b_im.h5";
  const regionA = "vol_a_mask_pc2.h5";
  const regionB = "vol_b_mask_pc2.h5";
  const labelA = "vol_a_im_xy.h5";
  const labelB = "vol_b_im_xy.h5";
  const regionDir = "/data/nag/region";
  const labelDir = "/data/nag/labels";

  const multiResult = () => ({
    image_directory: imagePath,
    region_mask_directory: regionDir,
    mask_directory: labelDir,
    image_files: [
      { name: imgA, path: `${imagePath}/${imgA}`, extension: ".h5", size: 1 },
      { name: imgB, path: `${imagePath}/${imgB}`, extension: ".h5", size: 1 },
    ],
    region_mask_files: [
      { name: regionA, path: `${regionDir}/${regionA}`, extension: ".h5", size: 1 },
      { name: regionB, path: `${regionDir}/${regionB}`, extension: ".h5", size: 1 },
    ],
    mask_files: [
      { name: labelA, path: `${labelDir}/${labelA}`, extension: ".h5", size: 1 },
      { name: labelB, path: `${labelDir}/${labelB}`, extension: ".h5", size: 1 },
    ],
    pairs: [],
    region_by_image: {},
    unmatched_images: [imgA, imgB],
    unmatched_region_masks: [regionA, regionB],
    unmatched_masks: [labelA, labelB],
    extra_channels: [],
    pairing_source: "filename",
    split: "",
    suggestions: { images: [], masks: [] },
    dataset_metadata: {},
    manifest_path: "",
  });

  beforeEach(() => {
    harness.scan.mockReset().mockResolvedValue(multiResult());
    harness.register.mockReset();
    harness.snapshot.mockReset().mockResolvedValue(null);
    harness.reconcile.mockReset();
  });

  it("hides a chosen region/label file from other row dropdowns", async () => {
    render(
      <MemoryRouter>
        <RegisterDataPage />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText(/Raw image directory/), {
      target: { value: imagePath },
    });
    fireEvent.change(screen.getByLabelText(/Region mask directory/), {
      target: { value: regionDir },
    });
    fireEvent.change(screen.getByLabelText(/Editable labels directory/), {
      target: { value: labelDir },
    });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));
    await screen.findByText(imgA);

    const regionSelects = screen.getAllByDisplayValue("— none —");
    const labelSelects = screen.getAllByDisplayValue("— image only —");
    expect(regionSelects).toHaveLength(2);
    expect(labelSelects).toHaveLength(2);

    fireEvent.change(regionSelects[0], { target: { value: regionA } });
    fireEvent.change(labelSelects[0], { target: { value: labelA } });

    // Re-query after re-render; row 0 keeps its choice, row 1 must not offer it.
    const regionRows = screen.getAllByRole("combobox").filter((el) =>
      Array.from((el as HTMLSelectElement).options).some((o) =>
        o.textContent?.includes("mask_pc2"),
      ),
    );
    const labelRows = screen.getAllByRole("combobox").filter((el) =>
      Array.from((el as HTMLSelectElement).options).some(
        (o) =>
          o.textContent === "— image only —" ||
          o.textContent?.includes("_im_xy"),
      ),
    );

    expect(regionRows).toHaveLength(2);
    expect(labelRows).toHaveLength(2);

    const regionOptsRow1 = Array.from(
      (regionRows[0] as HTMLSelectElement).options,
    ).map((o) => o.value);
    const regionOptsRow2 = Array.from(
      (regionRows[1] as HTMLSelectElement).options,
    ).map((o) => o.value);
    expect(regionOptsRow1).toContain(regionA);
    expect(regionOptsRow1).toContain(regionB);
    expect(regionOptsRow2).not.toContain(regionA);
    expect(regionOptsRow2).toContain(regionB);

    const labelOptsRow1 = Array.from(
      (labelRows[0] as HTMLSelectElement).options,
    ).map((o) => o.value);
    const labelOptsRow2 = Array.from(
      (labelRows[1] as HTMLSelectElement).options,
    ).map((o) => o.value);
    expect(labelOptsRow1).toContain(labelA);
    expect(labelOptsRow1).toContain(labelB);
    expect(labelOptsRow2).not.toContain(labelA);
    expect(labelOptsRow2).toContain(labelB);
  });
});

describe("Register Data interrupted response reconciliation", () => {
  beforeEach(() => {
    harness.scan.mockReset().mockImplementation(
      async (_image: string, editable: string, region: string) => result(editable, region),
    );
    harness.register.mockReset().mockRejectedValue(new TypeError("Failed to fetch"));
    harness.snapshot.mockReset().mockResolvedValue({dataset: null, volumeIds: []});
    harness.reconcile.mockReset();
  });

  const submitRegistration = async () => {
    render(
      <MemoryRouter>
        <RegisterDataPage />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText(/Raw image directory/), {
      target: { value: imagePath },
    });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));
    await screen.findByText(imageName);
    fireEvent.change(screen.getByLabelText(/Project \*/), {
      target: { value: "7" },
    });
    fireEvent.change(screen.getByLabelText(/Dataset name/), {
      target: { value: "nag_p10_batch2" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Register 1 dataset/ }));
  };

  it("treats a lost response as success when the expected dataset volumes exist", async () => {
    harness.reconcile.mockResolvedValue({
      status: "complete",
      volumes: [{id: 91, name: "heart", dataset: 44}],
    });

    await submitRegistration();

    expect(await screen.findByText("nag_p10_batch2 (1)")).toBeTruthy();
    expect(harness.reconcile).toHaveBeenCalledWith(
      7,
      "nag_p10_batch2",
      1,
      {dataset: null, volumeIds: []},
    );
    expect(screen.queryByText(/Some directories could not be registered/)).toBeNull();
    expect(screen.queryByText(/Failed to fetch/)).toBeNull();
  });

  it("shows a real failure when reconciliation confirms the dataset is missing", async () => {
    harness.reconcile.mockResolvedValue({status: "missing"});

    await submitRegistration();

    expect(await screen.findByText(
      /Some directories could not be registered: nag_p10_batch2 \(Failed to fetch\)/,
    )).toBeTruthy();
    expect(screen.queryByText("nag_p10_batch2 (1)")).toBeNull();
  });

  it("uses a cautious message when project-state verification is inconclusive", async () => {
    harness.reconcile.mockResolvedValue({status: "inconclusive"});

    await submitRegistration();

    expect((await screen.findByRole("status")).textContent).toMatch(
      /Registration may have completed.*check the project Data tab/i,
    );
    expect(screen.queryByText(/Some directories could not be registered/)).toBeNull();
  });
});
