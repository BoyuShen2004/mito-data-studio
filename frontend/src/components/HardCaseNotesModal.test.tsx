import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { HardCase } from "../types/hardCase";
import HardCaseList from "./HardCaseList";
import HardCaseNotesModal from "./HardCaseNotesModal";

const api = vi.hoisted(() => ({
  listHardCaseMessages: vi.fn(),
  updateHardCaseNote: vi.fn(),
  addHardCaseMessage: vi.fn(),
  setHardCaseStatus: vi.fn(),
}));

vi.mock("../api/hardCases", () => api);

const hardCase: HardCase = {
  id: 8,
  token: "token",
  task: 2,
  task_status: "in_progress",
  project: 3,
  project_title: "Mito",
  volume: 4,
  volume_name: "v1",
  label_id: 17,
  note: "Initial reason",
  z_start: 0,
  z_end: 2,
  status: "open",
  revoked: false,
  created_by: 5,
  created_by_username: "alice",
  created_at: "2026-08-10T12:00:00Z",
  resolved_by: null,
  resolved_by_username: "",
  resolved_at: null,
  url: "/share/hard-case/token",
  app_url: "/hard-cases/8",
  can_annotate: true,
  can_take_down: true,
  can_edit_note: true,
  can_comment: true,
  message_count: 0,
};

describe("HardCaseNotesModal", () => {
  beforeEach(() => {
    api.listHardCaseMessages.mockReset().mockResolvedValue([]);
    api.updateHardCaseNote.mockReset();
    api.addHardCaseMessage.mockReset();
    api.setHardCaseStatus.mockReset();
  });

  it("shares one editable note and discussion flow", async () => {
    const onChanged = vi.fn();
    api.updateHardCaseNote.mockResolvedValue({ ...hardCase, note: "Refined reason" });
    api.addHardCaseMessage.mockResolvedValue({
      id: 11,
      hard_case: 8,
      author: 5,
      author_username: "alice",
      body: "Please inspect this edge.",
      created_at: "2026-08-10T13:00:00Z",
    });
    render(<HardCaseNotesModal hardCase={hardCase} onClose={vi.fn()} onChanged={onChanged} />);
    await screen.findByText("No replies yet.");

    fireEvent.change(screen.getByLabelText("Primary note"), { target: { value: "Refined reason" } });
    fireEvent.click(screen.getByRole("button", { name: "Save note" }));
    await waitFor(() => expect(api.updateHardCaseNote).toHaveBeenCalledWith(8, "Refined reason"));

    fireEvent.change(screen.getByLabelText("Discussion reply"), { target: { value: "  Please inspect this edge.  " } });
    fireEvent.click(screen.getByRole("button", { name: "Post" }));
    await screen.findByText("Please inspect this edge.");
    expect(api.addHardCaseMessage).toHaveBeenCalledWith(8, "Please inspect this edge.");
    expect(onChanged).toHaveBeenCalledTimes(2);
  });

  it("lets a non-owner read the primary note and post without editing it", async () => {
    render(
      <HardCaseNotesModal
        hardCase={{ ...hardCase, can_edit_note: false, can_take_down: false, can_annotate: false }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("Initial reason")).toBeTruthy();
    expect(screen.queryByLabelText("Primary note")).toBeNull();
    expect(screen.getByLabelText("Discussion reply")).toBeTruthy();
    await screen.findByText("No replies yet.");
  });

  it("opens the shared modal from the list Note button", async () => {
    render(
      <MemoryRouter>
        <HardCaseList cases={[hardCase]} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Note" }));
    expect(screen.getByRole("dialog", { name: "Notes · label #17" })).toBeTruthy();
    await screen.findByText("No replies yet.");
  });
});
