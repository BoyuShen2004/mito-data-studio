import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import MemberPicker from "./MemberPicker";

describe("MemberPicker", () => {
  it("leaves the empty member area quiet under Add annotator", () => {
    render(
      <MemberPicker
        label="Add annotator to new team"
        annotators={[]}
        members={[]}
        onAdd={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Add annotator to new team")).toBeTruthy();
    expect(screen.queryByText("No annotators yet.")).toBeNull();
    expect(screen.getByLabelText("Add annotator to new team members").textContent).toBe("");
  });
});
