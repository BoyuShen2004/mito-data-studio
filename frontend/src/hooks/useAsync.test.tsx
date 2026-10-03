import { act, renderHook, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useAsync } from "./useAsync";

it("refreshes through a stable callback without refetching on unrelated renders", async () => {
  const fetch = vi.fn().mockResolvedValueOnce("first").mockResolvedValueOnce("second");
  const { result, rerender } = renderHook(() => useAsync(fetch));
  const reload = result.current.reload;
  await waitFor(() => expect(result.current.data).toBe("first"));
  rerender();
  expect(result.current.reload).toBe(reload);
  expect(fetch).toHaveBeenCalledTimes(1);
  act(() => reload());
  await waitFor(() => expect(result.current.data).toBe("second"));
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("ignores a superseded response after changing records", async () => {
  let resolveOld!: (value: string) => void;
  const old = new Promise<string>(resolve => { resolveOld = resolve; });
  const { result, rerender } = renderHook(({ id }) => useAsync(() => id === 1 ? old : Promise.resolve("new"), [id]), { initialProps: { id: 1 } });
  rerender({ id: 2 });
  await waitFor(() => expect(result.current.data).toBe("new"));
  await act(async () => resolveOld("old"));
  expect(result.current.data).toBe("new");
});
