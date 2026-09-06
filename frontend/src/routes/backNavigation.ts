// Where "Back" should go when there is no in-app history entry to pop —
// i.e. the page was deep-linked, reloaded, or is the first page after login.
// In those cases we walk *up* the route hierarchy instead of leaving the app.
import { matchPath } from "react-router-dom";
import { effectiveRole, homePathForRole } from "./roles";
import type { HomeRole } from "./roles";

/** The parent route for `pathname`, or null if it is a role home (nothing above it). */
export function backFallbackFor(
  pathname: string,
  role: string | null | undefined,
): string | null {
  const effective: HomeRole = effectiveRole(role);
  const home = homePathForRole(role);
  if (pathname === home) return null;

  // A task's submit/review form sits under the task itself.
  const submit = matchPath("/tasks/:id/submit", pathname);
  if (submit) return `/tasks/${submit.params.id}`;

  // Viewer / editor for a task → task page (managers are redirected to the
  // merged volume page from there; annotators stay on task details).
  const taskViewer =
    matchPath("/viewer/tasks/:id", pathname) ||
    matchPath("/editor/tasks/:id", pathname);
  if (taskViewer) return `/tasks/${taskViewer.params.id}`;

  // Volume viewer → back to the volume page.
  const volumeViewer = matchPath("/viewer/volumes/:id", pathname);
  if (volumeViewer) return `/volumes/${volumeViewer.params.id}`;

  // One person → the People hub. A hard case's real parent is its project's
  // Cases tab, which only the loaded case knows — the page's breadcrumb links
  // there, so Back falls back to home rather than guessing a project id.
  if (matchPath("/people/:username", pathname)) return "/people";

  // Volume detail sits under its project for managers.
  const volumePage = matchPath("/volumes/:id", pathname);
  if (volumePage) return effective === "manager" ? "/projects" : home;

  // Projects hang off the project list, which only managers have;
  // a requester's own list is their dashboard.
  const underProjects = matchPath("/projects/:id", pathname);
  if (underProjects) return effective === "manager" ? "/projects" : home;

  // Everything else (/projects, /tasks/:id, /hard-cases/:id,
  // /submissions/:id/review, /register-data, /people) goes up to home.
  return home;
}
