import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  buildVolumePyramid,
  deleteVolume,
  editVolume,
  getVolume,
  volumeDependents,
} from "../api/volumes";
import { listProjectTasks } from "../api/tasks";
import Breadcrumb from "../components/Breadcrumb";
import DeleteButton from "../components/DeleteButton";
import StatusBadge from "../components/StatusBadge";
import ShareControl from "../components/ShareControl";
import { MetadataDetailsCard } from "../components/VolumeMeta";
import type { Volume } from "../types/volume";
import type { AnnotationTask } from "../types/task";
import { useAuth } from "../auth/AuthContext";
import { useAsync } from "../hooks/useAsync";
import { showError } from "../errorPopup";

/**
 * A volume is the **artifact** — the thing being annotated — the way a file in
 * GitHub's Code tab is the artifact and a pull request is the work item.
 *
 * So this page holds paths, shape, voxel size, dataset metadata and the
 * streaming-pyramid state, and it links to its task. It no longer renders the
 * task's own details: those live on `/tasks/:id`, which is now the same page
 * for a manager and an annotator. Neither page imports the other's components.
 *
 * Metadata is edited in place in the sidebar — there is no "edit mode" to
 * enter, and the Save button appears only once something actually changed.
 */
export default function VolumeDetailPage() {
  const { id } = useParams();
  const volumeId = Number(id);
  const { isManager, isRequester } = useAuth();
  const navigate = useNavigate();
  const vol = useAsync(() => getVolume(volumeId), [volumeId]);
  const tasks = useAsync(
    () =>
      vol.data
        ? listProjectTasks(vol.data.project).then((all) =>
            all.filter((t) => t.volume === volumeId),
          )
        : Promise.resolve([]),
    [vol.data, volumeId],
  );

  const [pyramidBusy, setPyramidBusy] = useState<"image" | "region" | null>(null);

  const building =
    vol.data?.streaming_status === "building" ||
    vol.data?.region_streaming_status === "building";
  useEffect(() => {
    if (!building) return;
    const timer = window.setInterval(vol.reload, 3000);
    return () => window.clearInterval(timer);
    // Reload is intentionally scoped to the state transition; useAsync's
    // reload function has a new identity on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [building]);

  const buildPyramid = async (layer: "image" | "region") => {
    setPyramidBusy(layer);
    try {
      await buildVolumePyramid(volumeId, layer);
      vol.reload();
    } catch (error) {
      showError(error instanceof Error ? error.message : "Could not queue pyramid build.");
    } finally {
      setPyramidBusy(null);
    }
  };

  if (vol.loading) return <p className="muted">Loading…</p>;
  if (vol.error) return <div className="error">{vol.error}</div>;
  if (!vol.data) return null;
  const v = vol.data;
  const task: AnnotationTask | undefined = (tasks.data ?? [])[0];
  const canEdit = isManager || isRequester;

  return (
    <div className="volume-page">
      <Breadcrumb
        items={[
          { label: task?.project_title || `Project #${v.project}`, to: `/projects/${v.project}` },
          { label: "Data", to: `/projects/${v.project}?tab=data` },
          { label: v.name },
        ]}
      />

      {/* Identity and navigation only — no verbs. */}
      <header className="volume-header">
        <div className="row task-title-line">
          <h1>{v.name}</h1>
          <StatusBadge value={v.label_type || "none"} />
        </div>
        <p className="muted">
          {v.dataset_name ? <>Dataset <strong>{v.dataset_name}</strong> · </> : null}
          {tasks.loading ? (
            "loading its task…"
          ) : task ? (
            <>
              <Link to={`/tasks/${task.id}`}>Task #{task.id}</Link> · {task.status.replace(/_/g, " ")}
            </>
          ) : (
            <>no task yet{isManager ? " — assign it from the project's Tasks tab" : ""}</>
          )}
        </p>
      </header>

      <div className="volume-body">
        <div className="volume-main">
          <MetadataDetailsCard volume={v} task={task} />
          <StreamingStatusCard
            volume={v}
            isManager={isManager}
            busy={pyramidBusy}
            onBuild={buildPyramid}
          />
        </div>

        {canEdit && (
          <VolumeMetadataSidebar
            volume={v}
            onSaved={() => {
              vol.reload();
              tasks.reload();
            }}
          />
        )}
      </div>

      {canEdit && (
        <>
          {isManager && (
            <section className="section-block">
              <div className="section-heading">
                <h2>Public access</h2>
                <p className="muted">A public link makes this volume readable without an account.</p>
              </div>
              <ShareControl
                scope="volume"
                projectId={v.project}
                datasetId={v.dataset ?? undefined}
                volumeId={v.id}
              />
            </section>
          )}
          <section className="danger-zone">
            <h2>Danger zone</h2>
            <p className="muted">
              Deleting a volume removes its task, submissions and hard cases. There is no undo.
            </p>
            <DeleteButton
              label={`volume "${v.name}"`}
              dependents={() => volumeDependents(v.id)}
              onDelete={(force) => deleteVolume(v.id, force)}
              onDone={() => navigate(`/projects/${v.project}?tab=data`)}
            />
          </section>
        </>
      )}
    </div>
  );
}

const STREAMING_COPY = {
  image: {
    ready: "Ready for smooth chunk streaming.",
    building: "Building the streaming pyramid; View and Annotate use the original source meanwhile.",
    failed: "The latest build failed; View and Annotate continue through the original source.",
    not_built: "No streaming pyramid yet; View and Annotate use the original source.",
  },
  region: {
    ready: "The region mask streams through chunks alongside the image.",
    building: "Building the region-mask pyramid; the Region overlay uses full layers meanwhile.",
    failed: "The latest region build failed; the Region overlay continues through full layers.",
    not_built: "No region-mask pyramid yet; the Region overlay uses full layers.",
  },
} as const;

const BUILD_LABEL = {
  image: { ready: "Rebuild pyramid", failed: "Retry pyramid", other: "Build pyramid" },
  region: { ready: "Rebuild region", failed: "Retry region", other: "Build region" },
} as const;

/** One layer's readiness plus its manager control. Both layers read the same,
 * because a recipient of either should not have to learn two vocabularies. */
function StreamingLayerRow({
  layer,
  state,
  error,
  isManager,
  busy,
  onBuild,
}: {
  layer: "image" | "region";
  state: "ready" | "building" | "failed" | "not_built";
  error?: string;
  isManager: boolean;
  busy: boolean;
  onBuild: () => void;
}) {
  const labels = BUILD_LABEL[layer];
  const action =
    state === "ready" ? labels.ready : state === "failed" ? labels.failed : labels.other;
  return (
    <div className="streaming-layer-row">
      <div className="row spread">
        <div>
          <div className="row">
            <strong>{layer === "image" ? "Image" : "Region mask"}</strong>
            <StatusBadge value={state} />
          </div>
          <p className="muted" style={{ marginBottom: 0 }} title={error || undefined}>{STREAMING_COPY[layer][state]}</p>
        </div>
        {isManager && (
          <button
            type="button"
            className="secondary"
            disabled={busy || state === "building"}
            onClick={onBuild}
          >
            {busy ? "Queueing…" : action}
          </button>
        )}
      </div>
    </div>
  );
}

export function StreamingStatusCard({
  volume,
  isManager,
  busy,
  onBuild,
}: {
  volume: Volume | AnnotationTask;
  isManager: boolean;
  /** Which layer is mid-request, so only that row's button says "Queueing…". */
  busy: "image" | "region" | null | boolean;
  onBuild: (layer: "image" | "region") => void;
}) {
  const state = volume.streaming_status ?? (volume.ready_streaming ? "ready" : "not_built");
  // "absent" is a volume with no ROI at all: there is nothing to build, so the
  // row is omitted rather than shown as an unbuilt derivative.
  const regionState = volume.region_streaming_status
    ?? (volume.has_region_mask ? (volume.region_ready_streaming ? "ready" : "not_built") : "absent");
  const busyLayer = busy === true ? "image" : busy || null;

  return (
    <section className="card streaming-status-card" aria-label="Streaming status">
      <h3 style={{ marginTop: 0 }}>Streaming</h3>
      <StreamingLayerRow
        layer="image"
        state={state}
        error={volume.streaming_error}
        isManager={isManager}
        busy={busyLayer === "image"}
        onBuild={() => onBuild("image")}
      />
      {regionState !== "absent" && (
        <StreamingLayerRow
          layer="region"
          state={regionState}
          error={volume.region_streaming_error}
          isManager={isManager}
          busy={busyLayer === "region"}
          onBuild={() => onBuild("region")}
        />
      )}
    </section>
  );
}

/**
 * Volume metadata, editable where it is shown.
 *
 * Rule 5: you do not enter a mode to change a field. Save appears only when
 * something differs from what the server holds, so the sidebar reads as
 * metadata until it is being edited.
 */
function VolumeMetadataSidebar({
  volume,
  onSaved,
}: {
  volume: Volume;
  onSaved: () => void;
}) {
  const [name, setName] = useState(volume.name);
  const [imagePath, setImagePath] = useState(volume.image_path);
  const [regionMaskPath, setRegionMaskPath] = useState(volume.region_mask_path);
  const [labelPath, setLabelPath] = useState(volume.label_path);
  const [labelType, setLabelType] = useState<string>(volume.label_type);
  const [busy, setBusy] = useState(false);

  const dirty =
    name !== volume.name ||
    imagePath !== volume.image_path ||
    regionMaskPath !== volume.region_mask_path ||
    labelPath !== volume.label_path ||
    labelType !== volume.label_type;

  const reset = () => {
    setName(volume.name);
    setImagePath(volume.image_path);
    setRegionMaskPath(volume.region_mask_path);
    setLabelPath(volume.label_path);
    setLabelType(volume.label_type);
  };

  const save = async () => {
    setBusy(true);
    try {
      const hasMask = Boolean(labelPath.trim());
      if (!hasMask && labelType !== "none") {
        showError("Without a mask path, label type must be none.");
        return;
      }
      if (hasMask && labelType === "none") {
        showError("With a mask path, label type cannot be none.");
        return;
      }
      await editVolume(volume.id, {
        name,
        image_path: imagePath,
        region_mask_path: regionMaskPath,
        label_path: labelPath,
        label_type: labelType,
      });
      onSaved();
    } catch (err) {
      showError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  const hasMask = Boolean(labelPath.trim());
  const labelOptions = hasMask ? ["partial", "prediction"] : ["none"];

  return (
    <aside className="volume-sidebar" aria-label="Volume metadata">
      <label className="field">
        <span>Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span>1 · Raw image path</span>
        <input value={imagePath} onChange={(e) => setImagePath(e.target.value)} />
      </label>
      <label className="field">
        <span>2 · Region mask path (read-only)</span>
        <input value={regionMaskPath} onChange={(e) => setRegionMaskPath(e.target.value)} />
      </label>
      <label className="field">
        <span>3 · Editable label path</span>
        <input
          value={labelPath}
          onChange={(e) => {
            const next = e.target.value;
            setLabelPath(next);
            if (!next.trim()) setLabelType("none");
            else if (labelType === "none") setLabelType("prediction");
          }}
        />
      </label>
      <label className="field">
        <span>Label type *</span>
        <select
          value={labelOptions.includes(labelType) ? labelType : labelOptions[0]}
          onChange={(e) => setLabelType(e.target.value)}
          disabled={labelOptions.length === 1}
        >
          {labelOptions.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      </label>
      {dirty && (
        <div className="row">
          <button type="button" onClick={save} disabled={busy}>
            {busy ? "Saving…" : "Save metadata"}
          </button>
          <button type="button" className="secondary" onClick={reset} disabled={busy}>
            Discard
          </button>
        </div>
      )}
    </aside>
  );
}
