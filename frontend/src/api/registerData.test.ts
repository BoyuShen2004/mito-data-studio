import { beforeEach, describe, expect, it, vi } from "vitest";
import { reconcileRegistration } from "./registerData";

const state = vi.hoisted(() => ({
  listDatasets: vi.fn(),
  listProjectVolumes: vi.fn(),
}));

vi.mock("./datasets", () => ({listDatasets: state.listDatasets}));
vi.mock("./volumes", () => ({listProjectVolumes: state.listProjectVolumes}));

describe("registration reconciliation", () => {
  beforeEach(() => {
    state.listDatasets.mockReset();
    state.listProjectVolumes.mockReset();
  });

  it("returns the expected new volumes when a lost POST completed", async () => {
    state.listDatasets.mockResolvedValue([{id: 44, name: "nag_p10_batch2"}]);
    state.listProjectVolumes.mockResolvedValue([
      {id: 91, dataset: 44, name: "crop-a", streaming_status: "building"},
      {id: 92, dataset: 44, name: "crop-b", streaming_status: "not_built"},
    ]);

    await expect(reconcileRegistration(
      7,
      "nag_p10_batch2",
      2,
      {dataset: null, volumeIds: []},
      {attempts: 1},
    )).resolves.toEqual({
      status: "complete",
      volumes: [
        {id: 91, dataset: 44, name: "crop-a", streaming_status: "building"},
        {id: 92, dataset: 44, name: "crop-b", streaming_status: "not_built"},
      ],
    });
  });

  it("requires the full expected volume count", async () => {
    state.listDatasets.mockResolvedValue([{id: 44, name: "nag_p10_batch2"}]);
    state.listProjectVolumes.mockResolvedValue([{id: 91, dataset: 44}]);

    await expect(reconcileRegistration(
      7,
      "nag_p10_batch2",
      2,
      {dataset: null, volumeIds: []},
      {attempts: 1},
    )).resolves.toEqual({status: "missing"});
  });

  it("rechecks briefly while the server worker is still committing", async () => {
    state.listDatasets
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{id: 44, name: "nag_p10_batch2"}]);
    state.listProjectVolumes
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{id: 91, dataset: 44}]);

    await expect(reconcileRegistration(
      7,
      "nag_p10_batch2",
      1,
      {dataset: null, volumeIds: []},
      {attempts: 2, delayMs: 0},
    )).resolves.toMatchObject({status: "complete", volumes: [{id: 91}]});
  });

  it("counts only volumes added after the pre-request snapshot", async () => {
    state.listDatasets.mockResolvedValue([{id: 44, name: "existing-dataset"}]);
    state.listProjectVolumes.mockResolvedValue([
      {id: 1, dataset: 44},
      {id: 2, dataset: 44},
    ]);

    await expect(reconcileRegistration(
      7,
      "existing-dataset",
      1,
      {dataset: {id: 44} as never, volumeIds: [1]},
      {attempts: 1},
    )).resolves.toMatchObject({status: "complete", volumes: [{id: 2}]});
  });

  it("stays cautious when the pre-request state was unavailable", async () => {
    state.listDatasets.mockResolvedValue([{id: 44, name: "nag_p10_batch2"}]);
    state.listProjectVolumes.mockResolvedValue([{id: 91, dataset: 44}]);

    await expect(reconcileRegistration(
      7,
      "nag_p10_batch2",
      1,
      null,
      {attempts: 1},
    )).resolves.toEqual({status: "inconclusive"});
  });
});
