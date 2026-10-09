import { Component, Suspense, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { availableProjectExtensions, projectExtensionSearch, projectExtensions } from "./registry";
import type { ProjectExtensionContext, ProjectExtensionDefinition } from "./types";

/** Render failures stay inside the extension workspace, below the core tabs. */
class ExtensionBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed
      ? <p className="error" role="alert">This extension could not be opened. Other project sections remain available. Reload this page to retry.</p>
      : this.props.children;
  }
}

export default function ProjectExtensions({ definitions = projectExtensions, ...context }:
  ProjectExtensionContext & { definitions?: readonly ProjectExtensionDefinition[] }) {
  const [params, setParams] = useSearchParams();
  const extensions = availableProjectExtensions(context, definitions);
  const selectedId = params.get("extension");
  const selected = extensions.find(extension => extension.id === selectedId);
  if (selectedId) {
    const View = selected?.component;
    return <section aria-label="Project extension workspace">
      <div className="section-heading">
        <button type="button" className="secondary" onClick={() => setParams({ tab: "extensions" })}>← All extensions</button>
      </div>
      {View ? <ExtensionBoundary key={`${context.project.id}:${selectedId}`}>
        <Suspense fallback={<p role="status">Loading {selected.title}…</p>}>
          <View {...context} />
        </Suspense>
      </ExtensionBoundary> : <p role="alert">This extension is unavailable in this project or deployment.</p>}
    </section>;
  }
  return <section aria-label="Project extensions">
    <div className="section-heading">
      <h2>Extensions</h2>
      <p className="muted">Research tools for this project. Choose an extension to open it.</p>
    </div>
    {extensions.length ? <div className="grid">
      {extensions.map(extension => <article className="card" key={extension.id}>
        <h3>{extension.title}</h3>
        <p>{extension.description}</p>
        <p className="muted">{extension.accessDescription(context)}</p>
        <button type="button" onClick={() => setParams(projectExtensionSearch(extension.id))}>Open {extension.title}</button>
      </article>)}
    </div> : <p>No extensions are enabled for this project.</p>}
  </section>;
}
