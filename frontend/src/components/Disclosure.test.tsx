import { useEffect } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import Disclosure from "./Disclosure";

it("mounts on first expansion, retains drafts on collapse, and exposes its controlled body", () => {
  const mounted = vi.fn();
  function Form() { useEffect(mounted, []); return <input aria-label="Pending note" defaultValue="" />; }
  const view = render(<Disclosure title="Details" count={2}><Form /></Disclosure>);
  const toggle = screen.getByRole("button", { name: "Details" });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(mounted).not.toHaveBeenCalled();
  const body = document.getElementById(toggle.getAttribute("aria-controls")!);
  expect(body?.hidden).toBe(true);
  fireEvent.click(toggle);
  fireEvent.change(screen.getByLabelText("Pending note"), { target: { value: "Keep this draft" } });
  fireEvent.click(toggle);
  expect(body?.hidden).toBe(true);
  expect(screen.queryByRole("textbox")).toBeNull();
  view.rerender(<Disclosure title="Details" count={3}><Form /></Disclosure>);
  fireEvent.click(toggle);
  expect((screen.getByLabelText("Pending note") as HTMLInputElement).value).toBe("Keep this draft");
  expect(mounted).toHaveBeenCalledOnce();
});

it("keeps sibling disclosures independent", () => {
  render(<><Disclosure title="First"><p>First body</p></Disclosure><Disclosure title="Second"><p>Second body</p></Disclosure></>);
  fireEvent.click(screen.getByRole("button", { name: "First" }));
  expect(screen.getByRole("button", { name: "Second" }).getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByText("Second body")).toBeNull();
});

it("shows singleton content directly and preserves it when the collection grows", () => {
  const view = render(<Disclosure title="Collection" collapsible={false}><input aria-label="Draft" /></Disclosure>);
  expect(screen.queryByRole("button", { name: "Collection" })).toBeNull();
  fireEvent.change(screen.getByLabelText("Draft"), { target: { value: "Keep me" } });
  view.rerender(<Disclosure title="Collection" collapsible><input aria-label="Draft" /></Disclosure>);
  expect(screen.getByRole("button", { name: "Collection" }).getAttribute("aria-expanded")).toBe("true");
  expect((screen.getByLabelText("Draft") as HTMLInputElement).value).toBe("Keep me");
});
