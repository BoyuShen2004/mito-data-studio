import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import PeoplePage from "./PeoplePage";

const api = vi.hoisted(() => ({ getPeopleOverview: vi.fn(), updateMyProfile: vi.fn(), refresh: vi.fn() }));
vi.mock("../api/people", () => api);
vi.mock("../auth/AuthContext", () => ({ useAuth: () => ({ user: { username: "manager" }, refresh: api.refresh }) }));
vi.mock("../api/collaboration", () => ({ getCollaboration: vi.fn().mockResolvedValue({ users: [], teams: [] }) }));
vi.mock("../api/projects", () => ({ listProjects: vi.fn().mockResolvedValue([]) }));
const me = { id: 1, username: "manager", display_name: "Manager", role: "manager", institution_name: "Lab", contact_note: "", email: "" };
const overview = { me, role: "manager", annotators: [
  { ...me, id: 2, username: "alice", display_name: "Alice", role: "annotator", contact_note: "Alice contact details" },
  { ...me, id: 3, username: "bob", display_name: "Bob", role: "annotator", contact_note: "Bob contact details" },
], requesters: [], managers: [], peers: [], projects: [] };
beforeEach(() => { api.getPeopleOverview.mockReset().mockResolvedValue(overview); api.updateMyProfile.mockReset().mockResolvedValue(me); });
const open = () => render(<MemoryRouter><PeoplePage /></MemoryRouter>);

it("shows section summaries first and expands only the selected person", async () => {
  open();
  const section = await screen.findByRole("button", { name: "Annotators" });
  expect(screen.queryByRole("button", { name: "Alice" })).toBeNull();
  fireEvent.click(section);
  expect(screen.queryByText("Alice contact details")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Alice" }));
  expect(screen.getByText("Alice contact details")).toBeTruthy();
  expect(screen.queryByText("Bob contact details")).toBeNull();
  expect(screen.getByRole("link", { name: "Alice" }).getAttribute("href")).toBe("/people/alice");
  expect(screen.queryByRole("button", { name: "Customers (requesters)" })).toBeNull();
  expect(screen.getByRole("heading", { name: /Customers \(requesters\)/ })).toBeTruthy();
});

it("keeps expanded people mounted during an unrelated profile refresh", async () => {
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Annotators" }));
  fireEvent.click(screen.getByRole("button", { name: "Alice" }));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  api.getPeopleOverview.mockImplementationOnce(() => new Promise(() => {}));
  fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
  await screen.findByText("Refreshing people…");
  expect(screen.getByText("Alice contact details")).toBeTruthy();
  await waitFor(() => expect(screen.getByRole("button", { name: "Alice" }).getAttribute("aria-expanded")).toBe("true"));
});

it("retains scoped non-manager sections without exposing team management", async () => {
  api.getPeopleOverview.mockResolvedValue({ ...overview, role: "annotator", managers: [me], peers: overview.annotators, annotators: [], requesters: [] });
  open();
  await screen.findByRole("heading", { name: /Your manager\(s\)/ });
  expect(screen.queryByRole("button", { name: "Teams & assignment eligibility" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Customers (requesters)" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Annotators on your projects" }));
  expect(screen.getByRole("button", { name: "Alice" })).toBeTruthy();
});

it("shows the profile and a singleton roster directly without disclosure buttons", async () => {
  api.getPeopleOverview.mockResolvedValue({ ...overview, annotators: [overview.annotators[0]] });
  open();
  await screen.findByRole("heading", { name: "Your profile" });
  expect(screen.getByRole("button", { name: "Edit" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Your profile" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Annotators" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Alice" })).toBeNull();
  expect(screen.getByText("Alice contact details")).toBeTruthy();
});
