import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { showError } from "./errorPopup";

describe("showError", () => {
  let alert: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.useFakeTimers();
    alert = vi.spyOn(window, "alert").mockImplementation(() => {});
  });

  afterEach(() => {
    alert.mockRestore();
    vi.useRealTimers();
  });

  it("shows the same failure once when it is reported twice in a row", () => {
    showError("Prediction failed");
    showError("Prediction failed");
    expect(alert).toHaveBeenCalledTimes(1);
  });

  it("drops a message already contained in the one just shown", () => {
    showError("Label layer unavailable: lock denied");
    showError("lock denied");
    expect(alert).toHaveBeenCalledTimes(1);
  });

  it("still shows a different failure", () => {
    showError("Save failed");
    showError("Could not copy the link.");
    expect(alert).toHaveBeenCalledTimes(2);
  });

  it("shows the same failure again once the moment has passed", () => {
    showError("Prediction failed");
    vi.advanceTimersByTime(3001);
    showError("Prediction failed");
    expect(alert).toHaveBeenCalledTimes(2);
  });
});
