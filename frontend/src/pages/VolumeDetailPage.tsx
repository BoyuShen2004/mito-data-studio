import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  buildVolumePyramid,
  deleteVolume,
  editVolume,
  getVolume,
  volumeDependents,
} from "../api/volumes";
import {
  listProjectTasks,
} from "../api/tasks";
import DeleteButton from "../components/DeleteButton";
import StatusBadge from "../components/StatusBadge";
import { TaskDetailsStack } from "../components/TaskDetailsCards";
import type { Volume } from "../types/volume";
import type { AnnotationTask } from "../types/task";
import { useAuth } from "../auth/AuthContext";
import { useAsync } from "../hooks/useAsync";
import ShareControl from "../components/ShareControl";

/** One page for a volume: volume metadata + its task's details (no separate
 * task-Details hop). A volume is one assignable unit, so there is exactly one
 * task here — the list stays a list only so a volume with none renders too.
 * Volume edits stay in the Metadata card; task assignment fields live in the
 * project's Assign details so Metadata and Task never merge by role or mode. */
export default function VolumeDetailPage() {
  const { id } = useParams();
  const volumeId = Number(id);
  const { user, isManager, isRequester } = useAuth();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
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
  const [pyramidNotice, setPyramidNotice] = useState<string | null>(null);

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
    setPyramidNotice(null);
    try {
      await buildVolumePyramid(volumeId, layer);
      setPyramidNotice(
        layer === "region"
          ? "Region-mask build queued. The Region overlay keeps using full layers while it builds."
          : "Pyramid build queued. The original source remains available while it builds.",
      );
      vol.reload();
    } catch (error) {
      setPyramidNotice(error instanceof Error ? error.message : "Could not queue pyramid build.");
    } finally {
      setPyramidBusy(null);
    }
  };

  if (vol.loading) return <p className="muted">Loading…</p>;
  if (vol.error) return <div className="error">{vol.error}</div>;
  if (!vol.data) return null;
  const v = vol.data;
  const taskList = tasks.data ?? [];
  const primaryTask = taskList[0];

  return (
    <>
      <div className="row spread">
        <h1>{v.name}</h1>
        <div className="row">
          {isManager && (
            <ShareControl
              scope="volume"
              projectId={v.project}
              datasetId={v.dataset ?? undefined}
              volumeId={v.id}
            />
          )}
          <Link to={`/projects/${v.project}`}>
            <button type="button" className="secondary">
              Project
            </button>
          </Link>
          {(isManager || isRequester) && <button
            type="button"
            className="secondary"
            onClick={() => setEditing((e) => !e)}
          >
            {editing ? "Close" : "Edit metadata"}
          </button>}
          {(isManager || isRequester) && <DeleteButton
            label={`volume "${v.name}"`}
            dependents={() => volumeDependents(v.id)}
            onDelete={(force) => deleteVolume(v.id, force)}
            onDone={() => navigate(`/projects/${v.project}`)}
          />}
        </div>
      </div>

      {v.dataset_name && (
        <p className="muted">
          Dataset: <strong>{v.dataset_name}</strong>
        </p>
      )}

      <TaskDetailsStack
        volume={v}
        tasks={taskList}
        primaryTask={primaryTask}
        streamingCard={<StreamingStatusCard
          volume={v}
          isManager={isManager}
          busy={pyramidBusy}
          notice={pyramidNotice}
          onBuild={buildPyramid}
        />}
        // Edit mode swaps the Metadata card only — Task # stays its own card.
        metadataCard={editing ? (
          <div className="card">
            <h3>Metadata</h3>
            <VolumeEditForm
              volume={v}
              onSaved={() => {
                setEditing(false);
                vol.reload();
                tasks.reload();
              }}
              onCancel={() => setEditing(false)}
            />
          </div>
        ) : undefined}
        emptyMetadata={
          <div className="card"><h3>Metadata</h3><p className="muted">Task metadata is not available yet.</p></div>
        }
        taskActions={(t) => <>
          <Link to={`/viewer/tasks/${t.id}`}><button type="button" className="secondary">View</button></Link>
          {(t.assigned_to === user?.id || isManager) && t.can_annotate && (
            <Link to={`/editor/tasks/${t.id}`}><button type="button">Annotate</button></Link>
          )}
        </>}
      />

      {tasks.loading && <p className="muted">Loading tasks…</p>}
      {!tasks.loading && taskList.length === 0 && (
        <div className="card">
          <h3>Task</h3>
          <p className="muted" style={{ marginBottom: 0 }}>
            No task yet
            {isManager
              ? " — open the project's Assign tab to turn this volume into its whole-volume task."
              : "."}
          </p>
        </div>
      )}
    </>
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
          <p className="muted" style={{ marginBottom: 0 }}>{STREAMING_COPY[layer][state]}</p>
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
      {error && <div className="error">{error}</div>}
    </div>
  );
}

export function StreamingStatusCard({
  volume,
  isManager,
  busy,
  notice,
  onBuild,
}: {
  volume: Volume | AnnotationTask;
  isManager: boolean;
  /** Which layer is mid-request, so only that row's button says "Queueing…". */
  busy: "image" | "region" | null | boolean;
  notice: string | null;
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
      {notice && <p className="error" role="alert">{notice}</p>}
    </section>
  );
}

/** Edit only volume metadata. Task fields have one home: Assign Details. */
function VolumeEditForm({
  volume,
  onSaved,
  onCancel,
}: {
  volume: Volume;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(volume.name);
  const [imagePath, setImagePath] = useState(volume.image_path);
  const [regionMaskPath, setRegionMaskPath] = useState(volume.region_mask_path);
  const [labelPath, setLabelPath] = useState(volume.label_path);
  const [labelType, setLabelType] = useState<string>(volume.label_type);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const hasMask = Boolean(labelPath.trim());
      if (!hasMask && labelType !== "none") {
        setError("Without a mask path, label type must be none.");
        return;
      }
      if (hasMask && labelType === "none") {
        setError("With a mask path, label type cannot be none.");
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
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  };

  const hasMask = Boolean(labelPath.trim());
  const labelOptions = hasMask ? ["partial", "prediction"] : ["none"];
  return (
    <div className="edit-form" style={{ margin: 0 }}>
      {error && <div className="error">{error}</div>}
      <h4 style={{ marginTop: 0 }}>Volume</h4>
      <div className="row fields">
        <label className="field">
          <span>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
      </div>
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
      <label className="field" style={{ maxWidth: "16rem" }}>
        <span>Label type *</span>
        <select
          value={labelOptions.includes(labelType) ? labelType : labelOptions[0]}
          onChange={(e) => setLabelType(e.target.value)}
          disabled={labelOptions.length === 1}
        >
          {labelOptions.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </label>

      <div className="row" style={{ marginTop: "0.75rem" }}>
        <button type="button" onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save metadata"}
        </button>
        <button type="button" className="secondary" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
