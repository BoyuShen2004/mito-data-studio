import type { Axis } from "../../api/viewer";

export interface ViewLocation {
  z: number;
  y: number;
  x: number;
  axis: Axis;
  label?: number;
}

export function hasViewLocation(search: string): boolean {
  const params = new URLSearchParams(search);
  return ["z", "y", "x", "axis", "label"].some((key) => params.has(key));
}

const coordinate = (params: URLSearchParams, key: "z" | "y" | "x") => {
  const value = Number(params.get(key));
  return Number.isFinite(value) && value >= 0 ? Math.floor(value) : 0;
};

/** Parse viewer position only; the public-share token remains the authority. */
export function parseViewLocation(search: string): ViewLocation {
  const params = new URLSearchParams(search);
  const requestedAxis = params.get("axis");
  const axis: Axis = requestedAxis === "x" || requestedAxis === "y" ? requestedAxis : "z";
  const label = Number(params.get("label"));
  return {
    z: coordinate(params, "z"),
    y: coordinate(params, "y"),
    x: coordinate(params, "x"),
    axis,
    ...(Number.isFinite(label) && label > 0 ? {label: Math.floor(label)} : {}),
  };
}

export function withViewLocation(rawUrl: string, location?: ViewLocation | null): string {
  const url = new URL(rawUrl, window.location.origin);
  for (const key of ["z", "y", "x", "axis", "label"]) url.searchParams.delete(key);
  if (!location) return url.toString();
  url.searchParams.set("z", String(location.z));
  url.searchParams.set("y", String(location.y));
  url.searchParams.set("x", String(location.x));
  url.searchParams.set("axis", location.axis);
  if (location.label && location.label > 0) url.searchParams.set("label", String(location.label));
  return url.toString();
}
