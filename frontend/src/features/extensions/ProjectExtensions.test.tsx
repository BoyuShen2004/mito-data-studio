import { lazy } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { expect, it, vi } from "vitest";
import ProjectExtensions from "./ProjectExtensions";
import type { ProjectExtensionContext, ProjectExtensionDefinition } from "./types";

const context = { project: { id: 4 }, isManager: false, volumes: { data: [], loading: false, error: null, reload: vi.fn() } } as unknown as ProjectExtensionContext;
const definition = (id: string, load: ProjectExtensionDefinition['component']): ProjectExtensionDefinition => ({
  id, title: id, description: `Research tool ${id}`, accessDescription: () => "Read project results", enabled: true, component: load,
});
function Location() { return <output aria-label="Current URL">{useLocation().search}</output>; }
function open(definitions: ProjectExtensionDefinition[], url = "/projects/4?tab=extensions") {
  render(<MemoryRouter initialEntries={[url]}>
    <Location />
    <ProjectExtensions {...context} definitions={definitions} />
  </MemoryRouter>);
}

it("lists a second extension without changing the host, and loads only the chosen tool", async () => {
  const first = vi.fn(async () => ({ default: () => <p>First extension workspace</p> }));
  const second = vi.fn(async () => ({ default: () => <p>Second extension workspace</p> }));
  open([definition("first", lazy(first)), definition("second", lazy(second))]);
  expect(first).not.toHaveBeenCalled(); expect(second).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Open second" }));
  expect(await screen.findByText("Second extension workspace")).toBeTruthy();
  expect(first).not.toHaveBeenCalled(); expect(second).toHaveBeenCalledOnce();
  expect(screen.getByLabelText("Current URL").textContent).toContain("extension=second");
  fireEvent.click(screen.getByRole("button", { name: /All extensions/ }));
  expect(screen.getByRole("button", { name: "Open first" })).toBeTruthy();
  expect(screen.queryByText("Second extension workspace")).toBeNull();
});
it("does not load disabled tools even when their URL is entered directly", () => {
  const load = vi.fn(async () => ({ default: () => <p>Disabled tool</p> }));
  open([{ ...definition("disabled", lazy(load)), enabled: false }], "/projects/4?tab=extensions&extension=disabled");
  expect(screen.getByRole("alert").textContent).toContain("unavailable");
  expect(load).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /All extensions/ }));
  expect(screen.getByText(/No extensions are enabled/)).toBeTruthy();
});
it("contains a failed extension load and leaves navigation back to the catalog usable", async () => {
  const log = vi.spyOn(console, "error").mockImplementation(() => {});
  open([definition("broken", lazy(async () => { throw new Error("Load failed"); }))], "/projects/4?tab=extensions&extension=broken");
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("could not be opened"));
  fireEvent.click(screen.getByRole("button", { name: /All extensions/ }));
  expect(screen.getByRole("button", { name: "Open broken" })).toBeTruthy();
  log.mockRestore();
});

it("handles removed extension links without loading another tool", () => {
  const load = vi.fn(async () => ({ default: () => <p>Available tool</p> }));
  open([definition("available", lazy(load))], "/projects/4?tab=extensions&extension=removed");
  expect(screen.getByRole("alert").textContent).toContain("unavailable");
  expect(load).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /All extensions/ }));
  expect(screen.getByRole("button", { name: "Open available" })).toBeTruthy();
});
