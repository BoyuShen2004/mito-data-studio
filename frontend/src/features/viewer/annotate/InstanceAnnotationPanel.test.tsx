import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import InstanceAnnotationPanel from "./InstanceAnnotationPanel";

const harness = vi.hoisted(() => ({
  fetchTask: vi.fn(),
  save: vi.fn(),
  byLabelId: (rows: { label_id: number }[]) =>
    new Map(rows.map((row) => [row.label_id, row])),
}));

vi.mock("../../../api/instanceAnnotations", () => ({
  fetchTaskInstanceAnnotations: harness.fetchTask,
  saveInstanceAnnotation: harness.save,
  byLabelId: harness.byLabelId,
}));

// Declared inside `vi.hoisted` because `vi.mock` is hoisted above ordinary
// top-level declarations — referencing a plain `class` here throws a TDZ error.
const { FakeApiError } = vi.hoisted(() => ({
  FakeApiError: class extends Error {
    status: number;
    constructor(status: number) {
      super("nope");
      this.status = status;
    }
  },
}));
vi.mock("../../../api/client", () => ({ ApiError: FakeApiError }));

const VOCABULARY = {
  morphology: [
    { value: "normal", label: "Normal" },
    { value: "swollen", label: "Swollen" },
  ],
  qa_flags: [
    { value: "uncertain", label: "Uncertain — needs a second look" },
    { value: "needs_split", label: "Under-segmented" },
  ],
};

function row(overrides: Record<string, unknown> = {}) {
  return {
    label_id: 7,
    morphology: "",
    qa_flags: [],
    note: "",
    review_worthy: false,
    updated_by: null,
    updated_at: null,
    ...overrides,
  };
}

describe("instance annotation panel", () => {
  beforeEach(() => {
    harness.fetchTask.mockReset().mockResolvedValue({
      task: 1,
      volume: 2,
      vocabulary: VOCABULARY,
      annotations: [],
    });
    harness.save.mockReset().mockImplementation((_t, labelId, patch) =>
      Promise.resolve(row({ label_id: labelId, ...patch })),
    );
  });

  it("renders nothing at all when the deployment has not enabled it", async () => {
    harness.fetchTask.mockRejectedValue(new FakeApiError(503));
    const { container } = render(
      <InstanceAnnotationPanel taskId={1} activeId={7} />,
    );
    await waitFor(() => expect(harness.fetchTask).toHaveBeenCalled());
    await waitFor(() => expect(container.innerHTML).toBe(""));
  });

  it("says unclassified is not the same as normal", async () => {
    render(<InstanceAnnotationPanel taskId={1} activeId={7} />);
    expect(
      await screen.findByText(/Unclassified — not the same as/),
    ).toBeTruthy();
  });

  it("sends only the field that changed, so the others survive", async () => {
    render(<InstanceAnnotationPanel taskId={1} activeId={7} />);
    fireEvent.click(await screen.findByRole("button", { name: "Swollen" }));
    await waitFor(() =>
      expect(harness.save).toHaveBeenCalledWith(1, 7, { morphology: "swollen" }),
    );
  });

  it("clicking the selected phenotype again clears it", async () => {
    harness.fetchTask.mockResolvedValue({
      task: 1,
      volume: 2,
      vocabulary: VOCABULARY,
      annotations: [row({ morphology: "swollen" })],
    });
    render(<InstanceAnnotationPanel taskId={1} activeId={7} />);
    const chip = await screen.findByRole("button", { name: "Swollen" });
    await waitFor(() => expect(chip.getAttribute("aria-pressed")).toBe("true"));
    fireEvent.click(chip);
    await waitFor(() =>
      expect(harness.save).toHaveBeenCalledWith(1, 7, { morphology: "" }),
    );
  });

  it("keeps morphology and flags independent", async () => {
    harness.fetchTask.mockResolvedValue({
      task: 1,
      volume: 2,
      vocabulary: VOCABULARY,
      annotations: [row({ morphology: "swollen" })],
    });
    render(<InstanceAnnotationPanel taskId={1} activeId={7} />);
    const flag = await screen.findByRole("checkbox", {
      name: /Uncertain/,
    });
    fireEvent.click(flag);
    // The flag write carries only the flags: the phenotype is untouched.
    await waitFor(() =>
      expect(harness.save).toHaveBeenCalledWith(1, 7, {
        qa_flags: ["uncertain"],
      }),
    );
  });

  it("prompts for a selection when nothing is active", async () => {
    render(<InstanceAnnotationPanel taskId={1} activeId={null} />);
    expect(
      await screen.findByText(/Select an instance to record its morphology/),
    ).toBeTruthy();
  });

  it("disables every control in read-only mode", async () => {
    render(<InstanceAnnotationPanel taskId={1} activeId={7} readOnly />);
    const chip = await screen.findByRole("button", { name: "Swollen" });
    expect((chip as HTMLButtonElement).disabled).toBe(true);
  });

  it("drops an unsaved note draft when the selection moves", async () => {
    const { rerender } = render(
      <InstanceAnnotationPanel taskId={1} activeId={7} />,
    );
    const note = (await screen.findByPlaceholderText(
      "Optional",
    )) as HTMLInputElement;
    fireEvent.change(note, { target: { value: "half typed" } });
    expect(note.value).toBe("half typed");

    rerender(<InstanceAnnotationPanel taskId={1} activeId={8} />);
    const moved = (await screen.findByPlaceholderText(
      "Optional",
    )) as HTMLInputElement;
    // The text must not follow the user onto a different instance.
    expect(moved.value).toBe("");
    expect(harness.save).not.toHaveBeenCalled();
  });

  it("flags a review-worthy instance in the header", async () => {
    harness.fetchTask.mockResolvedValue({
      task: 1,
      volume: 2,
      vocabulary: VOCABULARY,
      annotations: [row({ qa_flags: ["uncertain"], review_worthy: true })],
    });
    render(<InstanceAnnotationPanel taskId={1} activeId={7} />);
    expect(await screen.findByTitle("Flagged for review")).toBeTruthy();
  });
});
