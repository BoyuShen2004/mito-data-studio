import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CollaborationManager from "./CollaborationManager";

const collaborationApi = vi.hoisted(() => ({getCollaboration: vi.fn(), mutateCollaboration: vi.fn()}));
const projectApi = vi.hoisted(() => ({listProjects: vi.fn()}));
vi.mock("../api/collaboration", () => collaborationApi);
vi.mock("../api/projects", () => projectApi);

describe("CollaborationManager", () => {
  beforeEach(() => {
    collaborationApi.getCollaboration.mockReset().mockResolvedValue({
      institutions: [{id: 4, name: "Hidden organization"}],
      users: [
        {id: 10, username: "ann", role: "annotator"},
        {id: 12, username: "bob", role: "annotator"},
        {id: 11, username: "manager", role: "manager"},
      ],
      teams: [],
    });
    collaborationApi.mutateCollaboration.mockReset().mockResolvedValue({});
    projectApi.listProjects.mockReset().mockResolvedValue([{id: 7, title: "Mito Project", teams: [], working_team: null}]);
  });

  it("defaults to the project name and creates from annotators only", async () => {
    render(<MemoryRouter initialEntries={["/people?project=7"]}><CollaborationManager /></MemoryRouter>);
    const name = await screen.findByLabelText("New team name") as HTMLInputElement;
    await waitFor(() => expect(name.value).toBe("Mito Project"));
    expect(screen.queryByText("Institution…")).toBeNull();
    expect(screen.queryByText("Experience levels")).toBeNull();
    expect(screen.queryByLabelText(/manager/i)).toBeNull();

    const createPicker = screen.getByLabelText("Add annotator to new team");
    expect(name.closest(".team-editor-controls")).toBe(createPicker.closest(".team-editor-controls"));
    fireEvent.change(createPicker, {target: {value: "10"}});
    expect(screen.getByRole("button", {name: "Remove ann"})).toBeTruthy();
    expect(within(createPicker).queryByRole("option", {name: "ann"})).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "Create team"}));
    await waitFor(() => expect(collaborationApi.mutateCollaboration).toHaveBeenCalledWith({
      action: "create_team",
      name: "Mito Project",
      member_ids: [10],
      project_id: 7,
    }));
  });

  it("adds and removes existing-team members with one click", async () => {
    collaborationApi.getCollaboration.mockResolvedValue({
      institutions: [],
      users: [
        {id: 10, username: "ann", role: "annotator"},
        {id: 12, username: "bob", role: "annotator"},
      ],
      teams: [{id: 3, name: "Team A", members: [{user_id: 10, username: "ann", role: "member"}]}],
    });
    render(<MemoryRouter><CollaborationManager /></MemoryRouter>);
    const picker = await screen.findByLabelText("Add annotator to Team A");
    expect(within(picker).queryByRole("option", {name: "ann"})).toBeNull();
    fireEvent.change(picker, {target: {value: "12"}});
    await waitFor(() => expect(collaborationApi.mutateCollaboration).toHaveBeenCalledWith({
      action: "add_team_member", team_id: 3, user_id: 12,
    }));
    fireEvent.click(screen.getByRole("button", {name: "Remove ann"}));
    await waitFor(() => expect(collaborationApi.mutateCollaboration).toHaveBeenCalledWith({
      action: "remove_team_member", team_id: 3, user_id: 10,
    }));
  });

  it("confirms assigned-work consequences before deleting a team", async () => {
    collaborationApi.getCollaboration.mockResolvedValue({
      institutions: [],
      users: [{id: 10, username: "ann", role: "annotator"}],
      teams: [{
        id: 3,
        name: "Team A",
        members: [{user_id: 10, username: "ann", role: "member"}],
        delete_impact: {
          project_count: 1,
          task_count: 2,
          projects: [{id: 7, title: "Mito Project", task_count: 2}],
        },
      }],
    });
    projectApi.listProjects.mockResolvedValue([
      {id: 7, title: "Mito Project", teams: [3], working_team: 3},
    ]);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<MemoryRouter><CollaborationManager /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", {name: "Delete team"}));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("2 assignment(s) will be withdrawn"));
    await waitFor(() => expect(collaborationApi.mutateCollaboration).toHaveBeenCalledWith({
      action: "delete_team", team_id: 3, confirm: true,
    }));
    confirm.mockRestore();
  });
});
