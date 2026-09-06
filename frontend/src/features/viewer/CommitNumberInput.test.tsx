import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import CommitNumberInput from "./CommitNumberInput";

function Harness({
  initial = 1,
  onCommit,
}: {
  initial?: number;
  onCommit: (n: number) => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <CommitNumberInput
        value={value}
        min={1}
        max={100}
        onCommit={onCommit}
        ariaLabel="layer"
      />
      {/* Stands in for a background update — a slice finishing its load, or a
          layer change arriving from elsewhere in the viewer. */}
      <button type="button" onClick={() => setValue((v) => v + 1)}>
        background update
      </button>
    </>
  );
}

const field = () => screen.getByLabelText("layer") as HTMLInputElement;

describe("CommitNumberInput", () => {
  it("commits what was typed, clamped, on blur", () => {
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);
    fireEvent.change(field(), { target: { value: "42" } });
    fireEvent.blur(field());
    expect(onCommit).toHaveBeenCalledWith(42);
  });

  it("clamps out-of-range input rather than rejecting it", () => {
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);
    fireEvent.change(field(), { target: { value: "9999" } });
    fireEvent.blur(field());
    expect(onCommit).toHaveBeenCalledWith(100);
    expect(field().value).toBe("100");
  });

  it("keeps a background update from clobbering what the user typed", () => {
    // The bug this guards: `value` changing mid-edit re-synced the draft and
    // silently discarded the user's keystrokes, so the commit applied the old
    // number. Rare by hand, reliable under load — it made the viewer's layer
    // field intermittently ignore a typed layer.
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);

    fireEvent.focus(field());
    fireEvent.change(field(), { target: { value: "80" } });
    fireEvent.click(screen.getByRole("button", { name: "background update" }));

    expect(field().value).toBe("80");
    fireEvent.blur(field());
    expect(onCommit).toHaveBeenCalledWith(80);
  });

  it("protects the draft even when no focus event was fired", () => {
    // Programmatic input — and `fireEvent.change` — reach onChange without a
    // preceding focus event, so typing itself has to mark the field as edited.
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);
    fireEvent.change(field(), { target: { value: "55" } });
    fireEvent.click(screen.getByRole("button", { name: "background update" }));
    expect(field().value).toBe("55");
  });

  it("follows the prop again once the edit is over", () => {
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);
    fireEvent.change(field(), { target: { value: "7" } });
    fireEvent.blur(field());
    // Blur ended the edit, so a later background update is free to move it.
    fireEvent.click(screen.getByRole("button", { name: "background update" }));
    expect(field().value).toBe("2");
  });

  it("Escape abandons the edit and restores the current value", () => {
    const onCommit = vi.fn();
    render(<Harness initial={5} onCommit={onCommit} />);
    fireEvent.change(field(), { target: { value: "31" } });
    fireEvent.keyDown(field(), { key: "Escape" });
    expect(field().value).toBe("5");
    // And the field is no longer holding the prop off.
    fireEvent.click(screen.getByRole("button", { name: "background update" }));
    expect(field().value).toBe("6");
  });

  it("treats an emptied field as the minimum, not as a cancel", () => {
    // Existing behaviour, recorded rather than changed: `Number("")` is 0,
    // which is finite, so blurring an empty field clamps to `min` and commits
    // it. Clearing and retyping still works — this only decides what happens
    // if the user clears and then leaves.
    const onCommit = vi.fn();
    render(<Harness initial={3} onCommit={onCommit} />);
    fireEvent.change(field(), { target: { value: "" } });
    fireEvent.blur(field());
    expect(onCommit).toHaveBeenCalledWith(1);
    expect(field().value).toBe("1");
  });
});
