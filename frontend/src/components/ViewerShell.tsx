import type { ReactNode } from "react";

/**
 * The full-window viewer/editor shell — one module for every page that hosts
 * `AnnotationCanvas`/`SliceViewer` (task View, Annotate, volume viewer, and
 * the public hard-case share page).
 *
 * Why this exists: `.editor-shell` is `height: 100%` + `flex: 1`, which only
 * resolves against an ancestor that actually has a definite height. Authed
 * viewer routes get that from `Layout fullBleed` (`.layout-root-bleed`
 * = `100vh` → `.full-bleed-main`). The share page is deliberately mounted
 * *outside* `Layout` (no app navbar for a recipient with no account), so it
 * had no viewport ancestor at all — `height: 100%` collapsed to auto, the
 * canvas grew to its content height, and the page rendered as an infinitely
 * tall black scroll (progress/history/03-fix-hard-case-share-view.md item A).
 *
 * `standalone` wraps the same shell in `.full-bleed-standalone`, which
 * supplies exactly what `.layout-root-bleed` + `.full-bleed-main` supply
 * under the navbar — same classes, same box model, no share-only CSS fork.
 */
export default function ViewerShell({
  topbar,
  children,
  standalone = false,
}: {
  /** Contents of the `.editor-topbar` strip; omit for no topbar. */
  topbar?: ReactNode;
  children: ReactNode;
  /** True when the page is NOT inside `Layout fullBleed` (no app navbar). */
  standalone?: boolean;
}) {
  const shell = (
    <div className="editor-shell">
      {topbar != null && <div className="editor-topbar">{topbar}</div>}
      <div className="editor-body">{children}</div>
    </div>
  );
  return standalone ? <div className="full-bleed-standalone">{shell}</div> : shell;
}

/** Shell-shaped placeholder for a page's loading / error state — keeps the
 * same height contract as the real thing (a bare `<p>` inside `.editor-shell`
 * on a standalone page is what produced the black infinite scroll). */
export function ViewerShellMessage({
  children,
  tone = "muted",
  standalone = false,
}: {
  children: ReactNode;
  tone?: "muted" | "error";
  standalone?: boolean;
}) {
  return (
    <ViewerShell standalone={standalone}>
      <div className={tone === "error" ? "error" : "muted"} style={{ padding: "1rem" }}>
        {children}
      </div>
    </ViewerShell>
  );
}
