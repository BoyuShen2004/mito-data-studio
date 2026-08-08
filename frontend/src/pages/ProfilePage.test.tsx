import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProfilePage from "./ProfilePage";
import type { CurrentUser } from "../types";

const hoisted = vi.hoisted(() => ({
  updateMyProfile: vi.fn(),
  refresh: vi.fn(),
  user: { current: null as CurrentUser | null },
}));

vi.mock("../api/people", () => ({ updateMyProfile: hoisted.updateMyProfile }));
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: hoisted.user.current, refresh: hoisted.refresh }),
}));
vi.mock("../features/viewer/annotate/shortcutKeys", () => ({
  shortcutModifierLabel: () => "Ctrl",
}));

const defaults = { select: "v", brush: "b", eraser: "e", merge: "g", delete: "" };

function makeUser(overrides: Partial<CurrentUser> = {}): CurrentUser {
  return {
    id: 1,
    username: "ann",
    email: "",
    first_name: "",
    last_name: "",
    is_superuser: false,
    role: "annotator",
    institution_name: "",
    display_name: "",
    contact_note: "",
    annotate_shortcuts: { ...defaults },
    annotate_shortcut_defaults: { ...defaults },
    annotate_shortcut_tools: [
      { tool: "select", label: "Select" },
      { tool: "brush", label: "Brush" },
      { tool: "eraser", label: "Erase" },
      { tool: "merge", label: "Merge" },
      { tool: "delete", label: "Delete" },
    ],
    can_customize_shortcuts: true,
    ...overrides,
  } as CurrentUser;
}

const letterBox = (label: string) =>
  screen.getByLabelText(`${label} shortcut letter`) as HTMLInputElement;

describe("ProfilePage annotate shortcuts", () => {
  beforeEach(() => {
    hoisted.updateMyProfile.mockReset().mockResolvedValue(makeUser());
    hoisted.refresh.mockReset().mockResolvedValue(undefined);
    hoisted.user.current = makeUser();
  });

  it("shows the account's current binding for every tool", () => {
    render(<ProfilePage />);
    expect(letterBox("Brush").value).toBe("B");
    // An unbound tool shows an empty box, not a placeholder letter.
    expect(letterBox("Delete").value).toBe("");
  });

  it("saves the edited map to the server, not to this browser", async () => {
    render(<ProfilePage />);
    await userEvent.clear(letterBox("Brush"));
    await userEvent.type(letterBox("Brush"), "n");
    await userEvent.click(screen.getByRole("button", { name: "Save profile" }));

    await waitFor(() => expect(hoisted.updateMyProfile).toHaveBeenCalled());
    const sent = hoisted.updateMyProfile.mock.calls[0][0];
    expect(sent.annotate_shortcuts.brush).toBe("n");
    // The rest of the map rides along untouched, so a partial save cannot
    // silently drop bindings the server would then fill from defaults.
    expect(sent.annotate_shortcuts.merge).toBe("g");
  });

  it("blocks saving while two tools share a letter", async () => {
    render(<ProfilePage />);
    await userEvent.clear(letterBox("Brush"));
    await userEvent.type(letterBox("Brush"), "g"); // Merge already has G.

    expect(screen.getByRole("alert").textContent).toMatch(/cannot share a letter/i);
    expect((screen.getByRole("button", { name: "Save profile" }) as HTMLButtonElement).disabled)
      .toBe(true);
    expect(hoisted.updateMyProfile).not.toHaveBeenCalled();
  });

  it("only accepts letters", async () => {
    render(<ProfilePage />);
    await userEvent.clear(letterBox("Brush"));
    await userEvent.type(letterBox("Brush"), "4");
    expect(letterBox("Brush").value).toBe("");
  });

  it("restores the defaults without saving them", async () => {
    render(<ProfilePage />);
    await userEvent.clear(letterBox("Brush"));
    await userEvent.type(letterBox("Brush"), "n");
    await userEvent.click(screen.getByRole("button", { name: "Reset to defaults" }));

    expect(letterBox("Brush").value).toBe("B");
    expect(hoisted.updateMyProfile).not.toHaveBeenCalled();
  });

  it("tells a requester there is nothing to bind, and sends no map", async () => {
    hoisted.user.current = makeUser({ role: "requester", can_customize_shortcuts: false });
    render(<ProfilePage />);

    expect(screen.queryByLabelText("Brush shortcut letter")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Save profile" }));

    await waitFor(() => expect(hoisted.updateMyProfile).toHaveBeenCalled());
    expect(hoisted.updateMyProfile.mock.calls[0][0]).not.toHaveProperty("annotate_shortcuts");
  });
});
