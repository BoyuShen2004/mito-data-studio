import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
// Vite's `?raw` — jsdom lays nothing out, so widths are asserted on the sheet.
import css from "../styles.css?raw";
import AssignmentPlanEditor from "./AssignmentPlanEditor";

const taskApi = vi.hoisted(() => ({
  listPlanRows: vi.fn(),
  previewAssignPlan: vi.fn(),
  applyAssignPlan: vi.fn(),
  setTaskAnnotationLock: vi.fn(),
}));
const collaborationApi = vi.hoisted(() => ({
  getCollaboration: vi.fn(),
  mutateCollaboration: vi.fn(),
}));
vi.mock("../api/tasks", () => taskApi);
vi.mock("../api/collaboration", () => collaborationApi);

const row = {
  id: 41,
  project: 7,
  volume: 9,
  volume_name: "mito-volume",
  file_format: "tiff",
  shape_z: 20,
  shape_y: 256,
  shape_x: 512,
  voxel_size_z: 4,
  voxel_size_y: 1.5,
  voxel_size_x: 1.5,
  has_region_mask: true,
  region_mask_coverage: 0.25,
  label_type: "none",
  assigned_to: null,
  assigned_to_username: "",
  z_start: 0,
  z_end: 20,
  task_type: "manual_annotation",
  status: "unassigned",
  priority: 3,
  difficulty: 2,
  instructions: "",
  deadline: null,
  annotation_locked: false,
};

describe("AssignmentPlanEditor team-first assignment", () => {
  beforeEach(() => {
    taskApi.listPlanRows.mockReset().mockResolvedValue({
      created_tasks: 0,
      skipped_volumes: 0,
      entries: [row],
    });
    collaborationApi.getCollaboration.mockReset().mockResolvedValue({
      institutions: [],
      users: [
        { id: 10, username: "eligible-ann", role: "annotator" },
        { id: 11, username: "outsider", role: "annotator" },
      ],
      teams: [
        { id: 3, name: "Eligible Team", members: [{ user_id: 10, username: "eligible-ann" }] },
        { id: 4, name: "Other Team", members: [{ user_id: 11, username: "outsider" }] },
      ],
    });
    collaborationApi.mutateCollaboration.mockReset();
    taskApi.setTaskAnnotationLock.mockReset().mockResolvedValue({});
  });

  it("chooses one project working team and gives each row only an assignee control", async () => {
    render(
      <AssignmentPlanEditor
        projectId={7}
        projectTitle="Mito Project"
        workingTeamId={3}
      />,
    );
    const teamSelect = await screen.findByLabelText("Working team");
    expect((teamSelect as HTMLSelectElement).value).toBe("3");
    expect(within(teamSelect).getByRole("option", { name: "Eligible Team" })).toBeTruthy();
    expect(within(teamSelect).getByRole("option", { name: "Other Team" })).toBeTruthy();
    expect(screen.queryByLabelText("Assignment team")).toBeNull();

    const assigneeSelect = screen.getByLabelText("Assignee for mito-volume") as HTMLSelectElement;
    expect(assigneeSelect.disabled).toBe(false);
    expect(within(assigneeSelect).getByRole("option", { name: "eligible-ann" })).toBeTruthy();
    expect(within(assigneeSelect).queryByRole("option", { name: "outsider" })).toBeNull();

    // Task first with Status beside it, Details last. Status is on the main row
    // — a manager scans it across every row, so it must not cost a click per
    // row — and next to the id so that scan is one narrow column on the left.
    expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
      "Task", "Status", "Volume", "Format", "Shape (Z × Y × X)", "Voxel size (Z × Y × X)",
      "Region coverage", "Label type", "Assignee", "Details",
    ]);
    expect(screen.getByText("20 × 256 × 512")).toBeTruthy();
    expect(screen.getByText("4 × 1.5 × 1.5")).toBeTruthy();
    expect(screen.getByText("25%")).toBeTruthy();
    expect(screen.queryByLabelText("Instructions")).toBeNull();

    // Cells line up with those headers, Details being the last one in the row.
    const cells = screen.getAllByRole("cell");
    expect(cells[0].textContent).toBe("#41");
    expect(cells[1].textContent).toBe("unassigned");
    expect(cells[2].textContent).toBe("mito-volume");
    expect(cells[cells.length - 1].textContent).toContain("Details");

    const details = screen.getByRole("button", { name: "Details" });
    expect(details.closest("td")).toBe(cells[cells.length - 1]);
    fireEvent.click(details);
    expect(details.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByPlaceholderText("Notes for annotator").tagName).toBe("TEXTAREA");
    expect(screen.getByRole("button", { name: "Reset annotations" })).toBeTruthy();
    const close = screen.getByRole("button", { name: "Close annotation" });
    const reset = screen.getByRole("button", { name: "Reset annotations" });
    expect(close.compareDocumentPosition(reset) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Still called Details while open — only the caret turns, so the control
    // the user just clicked is still there to click again.
    expect(screen.getByRole("button", { name: "Details" })).toBe(details);
    expect(screen.queryByRole("button", { name: /Hide/ })).toBeNull();
    fireEvent.click(details);
    expect(details.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByPlaceholderText("Notes for annotator")).toBeNull();
  });

  it("keeps the Assignee select from reading as a wide empty dropdown", async () => {
    render(
      <AssignmentPlanEditor projectId={7} projectTitle="Mito Project" workingTeamId={3} />,
    );
    await screen.findByLabelText("Working team");
    const assignee = screen.getByLabelText("Assignee for mito-volume");
    expect(assignee.classList.contains("plan-assignee-select")).toBe(true);

    // jsdom computes no geometry, so the cap is asserted on the stylesheet.
    const rule = (selector: string) => {
      const wanted = selector.replace(/\s+/g, " ").trim();
      for (const [, head, body] of css.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
        if (head.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s+/g, " ").trim() === wanted) {
          return body;
        }
      }
      throw new Error(`no rule for ${selector}`);
    };
    expect(rule(".plan-assignee-select")).toMatch(/max-width:\s*9\.5rem/);
    // The long "Choose a working team first" option must not set the width.
    expect(rule(".plan-assignee-select")).toMatch(/min-width:\s*0/);
  });

  it("keeps a newly-created team selected and preserves row drafts", async () => {
    collaborationApi.mutateCollaboration.mockResolvedValue({
      institutions: [],
      users: [
        { id: 10, username: "eligible-ann", role: "annotator" },
        { id: 11, username: "outsider", role: "annotator" },
      ],
      teams: [
        { id: 3, name: "Eligible Team", members: [{ user_id: 10, username: "eligible-ann" }] },
        { id: 4, name: "Other Team", members: [{ user_id: 11, username: "outsider" }] },
        { id: 8, name: "Mito Project", members: [{ user_id: 11, username: "outsider" }] },
      ],
      mutation: { action: "create_team", team_id: 8 },
    });
    render(
      <AssignmentPlanEditor
        projectId={7}
        projectTitle="Mito Project"
        workingTeamId={null}
      />,
    );
    await screen.findByLabelText("Working team");
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    const priority = screen.getAllByRole("combobox").find((element) =>
      within(element).queryByRole("option", { name: /High/ }),
    ) as HTMLSelectElement;
    fireEvent.change(priority, { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Create team" }));
    expect((screen.getByLabelText("New team name") as HTMLInputElement).value).toBe("Mito Project");
    fireEvent.change(screen.getByLabelText("Add annotator to new team"), { target: { value: "11" } });
    fireEvent.click(screen.getByRole("button", { name: "Create team" }));
    await waitFor(() => expect((screen.getByLabelText("Working team") as HTMLSelectElement).value).toBe("8"));
    expect((priority as HTMLSelectElement).value).toBe("4");
    expect(within(screen.getByLabelText("Assignee for mito-volume")).getByRole("option", { name: "outsider" })).toBeTruthy();
  });
});
