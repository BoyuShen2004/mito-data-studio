// Role → home-route helpers. These live outside AppRoutes so that components
// rendered *by* AppRoutes (Layout, BackButton) can use them without an import
// cycle. AppRoutes re-exports them, so `from "../routes/AppRoutes"` still works.

export type HomeRole = "manager" | "requester" | "annotator";

export function effectiveRole(role: string | null | undefined): HomeRole {
  if (role === "manager") return "manager";
  if (role === "requester" || role === "client") return "requester";
  return "annotator";
}

/** Every role lands on the same personal home. The role still decides what
 * that page *contains* (see `pages/HomePage.tsx`), but not where it lives —
 * three role-specific roots were three URLs for one idea. */
export function homePathForRole(_role?: string | null | undefined): string {
  return "/";
}

/** Short label for the home — used on Home/Done buttons. */
export function homeLabelForRole(_role?: string | null | undefined): string {
  return "Home";
}
