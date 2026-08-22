import { useCallback, useState } from "react";
import {
  getAnnotatorTime,
  type AnnotatorTimeReport,
  type DatasetTimeRow,
  type ProjectTimeRow,
} from "../api/timing";
import { durationTitle } from "../time";

/**
 * Annotation time for one person: project → dataset → volume.
 *
 * **Lazy.** The People roster renders many of these, and most are never opened,
 * so nothing is fetched until someone expands the section. One request then
 * returns the whole folded tree, so expanding a project or a dataset afterwards
 * costs nothing.
 *
 * **Honest about what it does not know.** A project containing legacy-exempt
 * volumes has a real measured total *and* an unmeasured remainder. Showing only
 * the first would present a fraction as the whole, so every level that contains
 * legacy work carries a quiet `+ legacy` marker, and legacy volumes themselves
 * show `-` rather than being counted as zero.
 *
 * Expansion uses real `<button aria-expanded>` controls rather than clickable
 * divs, so the hierarchy is reachable and announced by a screen reader.
 */

function LegacyMark({ count }: { count: number }) {
  if (!count) return null;
  return (
    <span
      className="muted annotation-time-legacy"
      title={
        `${count} volume${count === 1 ? "" : "s"} here started before time ` +
        "tracking, so their time is unknown and not included in this total."
      }
    >
      + legacy
    </span>
  );
}

function VolumeRow({ volume }: { volume: DatasetTimeRow["volumes"][number] }) {
  return (
    <li className="annotation-time-volume">
      <span className="annotation-time-label" title={volume.volume_name}>
        {volume.volume_name}
      </span>
      <span
        className={`annotation-time${volume.tracked ? "" : " annotation-time-unknown"}`}
        title={durationTitle(volume.seconds, { legacy: !volume.tracked })}
      >
        {volume.display}
      </span>
    </li>
  );
}

function DatasetRow({ dataset }: { dataset: DatasetTimeRow }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="annotation-time-dataset">
      <button
        type="button"
        className="secondary annotation-time-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className="annotation-time-label">{dataset.dataset_name}</span>
        <span className="annotation-time">{dataset.display}</span>
        <LegacyMark count={dataset.legacy_volumes} />
      </button>
      {open && (
        <ul className="annotation-time-volumes">
          {dataset.volumes.map((volume) => (
            <VolumeRow key={volume.volume_id} volume={volume} />
          ))}
        </ul>
      )}
    </li>
  );
}

function ProjectRow({ project }: { project: ProjectTimeRow }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="annotation-time-project">
      <button
        type="button"
        className="secondary annotation-time-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className="annotation-time-label">{project.project_title}</span>
        <span className="annotation-time">{project.display}</span>
        <LegacyMark count={project.legacy_volumes} />
      </button>
      {open && (
        <ul className="annotation-time-datasets">
          {project.datasets.map((dataset) => (
            <DatasetRow
              key={dataset.dataset_id ?? `none-${dataset.dataset_name}`}
              dataset={dataset}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function AnnotatorTimeSection({
  username,
  heading = "Time",
}: {
  username: string;
  heading?: string;
}) {
  const [open, setOpen] = useState(false);
  const [report, setReport] = useState<AnnotatorTimeReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = useCallback(async () => {
    const next = !open;
    setOpen(next);
    if (!next || report || loading) return;
    setLoading(true);
    setError(null);
    try {
      setReport(await getAnnotatorTime(username));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load annotation time");
    } finally {
      setLoading(false);
    }
  }, [loading, open, report, username]);

  return (
    <section className="annotation-time-section" aria-label={`Annotation time for ${username}`}>
      <button
        type="button"
        className="secondary annotation-time-toggle annotation-time-root"
        aria-expanded={open}
        onClick={() => void toggle()}
      >
        <span aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className="annotation-time-label">{heading}</span>
        {report && <span className="annotation-time">{report.display}</span>}
        {report && <LegacyMark count={report.legacy_volumes} />}
      </button>
      {open && (
        <>
          {loading && <p className="muted annotation-time-empty">Loading…</p>}
          {error && <p className="error annotation-time-empty">{error}</p>}
          {report && report.projects.length === 0 && (
            <p className="muted annotation-time-empty">No annotation time recorded.</p>
          )}
          {report && report.projects.length > 0 && (
            <ul className="annotation-time-projects">
              {report.projects.map((project) => (
                <ProjectRow key={project.project_id} project={project} />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
