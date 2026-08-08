// Role → home-route helpers. These live outside AppRoutes so that components
// rendered *by* AppRoutes (Layout, BackButton) can use them without an import
// cycle. AppRoutes re-exports them, so `from "../routes/AppRoutes"` still works.

export type HomeRole = "manager" | "requester" | "annotator";

export function effectiveRole(role: string | null | undefined): HomeRole {
  if (role === "manager") return "manager";
  if (role === "requester" || role === "client") return "requester";
  return "annotator";
}

export function homePathForRole(role: string | null | undefined): string {
  return `/${effectiveRole(role)}`;
}

/** Short label for the role home — used on Home/Done buttons. */
export function homeLabelForRole(role: string | null | undefined): string {
  switch (effectiveRole(role)) {
    case "manager":
      return "Dashboard";
    case "requester":
      return "My Projects";
    default:
      return "My Tasks";
  }
}
