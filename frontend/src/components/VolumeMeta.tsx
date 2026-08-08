import { Fragment, type ReactNode } from "react";
import { METADATA_FIELDS } from "../metadataFields";
import type { DatasetMetadata } from "../types/project";
import RegionCoverage from "./RegionCoverage";
import StatusBadge from "./StatusBadge";

export interface VolumeMetaLike {
  row_key?: string | number;
  name?: string;
  file_format?: string | null;
  shape_z?: number | null;
  shape_y?: number | null;
  shape_x?: number | null;
  shape?: Array<number | null>;
  voxel_size_z?: number | null;
  voxel_size_y?: number | null;
  voxel_size_x?: number | null;
  voxel_size?: Array<number | null>;
  has_region_mask?: boolean;
  region_mask_path?: string;
  region_mask_location?: string;
  region_mask_coverage?: number | null;
  region_streaming_status?: "absent" | "not_built" | "building" | "ready" | "failed";
  label_type?: string | null;
}

function axes(volume: VolumeMetaLike, kind: "shape" | "voxel") {
  const packed = kind === "shape" ? volume.shape : volume.voxel_size;
  if (packed) return packed;
  return kind === "shape"
    ? [volume.shape_z ?? null, volume.shape_y ?? null, volume.shape_x ?? null]
    : [volume.voxel_size_z ?? null, volume.voxel_size_y ?? null, volume.voxel_size_x ?? null];
}

export function formatShape(volume: VolumeMetaLike): string {
  const values = axes(volume, "shape");
  return values.every((value) => value == null) ? "—" : values.map((value) => value ?? "?").join(" × ");
}

export function formatVoxelSize(volume: VolumeMetaLike): string {
  const values = axes(volume, "voxel");
  return values.every((value) => value == null) ? "—" : values.map((value) => value ?? "?").join(" × ");
}

/** Canonical volume facts. Missing scientific values remain explicitly visible. */
export function VolumeMetaBlock({
  volume,
  title,
  scientificMetadata,
}: {
  volume: VolumeMetaLike;
  title?: string;
  scientificMetadata?: DatasetMetadata | null;
}) {
  return <div className="volume-meta-block">
    {title && <h3>{title}</h3>}
    <dl className="meta-grid">
      {volume.name && <><dt>Name</dt><dd className="truncate" title={volume.name}>{volume.name}</dd></>}
      <dt>Format</dt><dd>{volume.file_format || "—"}</dd>
      <dt>Shape (Z × Y × X)</dt><dd className="mono-cell">{formatShape(volume)}</dd>
      <dt>Voxel size (Z × Y × X)</dt><dd className="mono-cell">{formatVoxelSize(volume)}</dd>
      <dt>Label type</dt><dd><StatusBadge value={volume.label_type || "none"}/></dd>
      <dt>Region coverage</dt><dd><RegionCoverage hasMask={Boolean(volume.has_region_mask)} coverage={volume.region_mask_coverage}/></dd>
      {scientificMetadata !== undefined && METADATA_FIELDS.map(({key, label}) => {
        const value = scientificMetadata?.[key];
        const display = value == null || String(value).trim() === "" ? "-" : String(value);
        return <Fragment key={String(key)}><dt>{label}</dt><dd>{display}</dd></Fragment>;
      })}
    </dl>
  </div>;
}

/** Shared table used by authenticated project data and anonymous shares.
 *
 * ``streaming`` renders the per-layer readiness badges under a column named
 * for exactly that. It used to be a general-purpose ``extra`` slot headed
 * **Details**, which invited unrelated scraps — a task count ended up stacked
 * above the badges, in a cell whose one job is streaming state.
 *
 * There is deliberately no per-volume task column: a volume is one assignable
 * unit, so the count is always 1 and never told a reader anything. */
export function DatasetVolumesTable({
  volumes,
  action,
  project,
  status,
  assignee,
  streaming,
  details,
  tableClassName = "",
  actionLabel = "Actions",
  actionAlign = "start",
}: {
  volumes: VolumeMetaLike[];
  action?: (volume: VolumeMetaLike) => ReactNode;
  project?: (volume: VolumeMetaLike) => ReactNode;
  status?: (volume: VolumeMetaLike) => ReactNode;
  assignee?: (volume: VolumeMetaLike) => ReactNode;
  streaming?: (volume: VolumeMetaLike) => ReactNode;
  details?: (volume: VolumeMetaLike) => ReactNode;
  tableClassName?: string;
  actionLabel?: string;
  actionAlign?: "start" | "center";
}) {
  const actionClassName = `col-open action-align-${actionAlign}`;
  return <div className="table-wrap volume-meta-table-wrap">
    <table className={`volume-meta-table${project ? " has-project" : ""}${status ? " has-status" : ""}${assignee ? " has-assignee" : ""}${tableClassName ? ` ${tableClassName}` : ""}`}>
      <colgroup>
        {project && <col className="col-meta-project"/>}
        <col className="col-meta-volume"/><col className="col-meta-format"/>
        <col className="col-meta-shape"/><col className="col-meta-voxel"/>
        <col className="col-meta-coverage"/><col className="col-meta-label"/>
        {status && <col className="col-meta-status"/>}
        {assignee && <col className="col-meta-assignee"/>}
        {streaming && <col className="col-meta-streaming"/>}
        {details && <col className="col-meta-details"/>}
        {action && <col className="col-meta-actions"/>}
      </colgroup>
      <thead><tr>
        {project && <th>Project</th>}
        <th>Volume</th><th>Format</th><th>Shape (Z × Y × X)</th>
        <th>Voxel size (Z × Y × X)</th><th>Region coverage</th><th>Label type</th>
        {status && <th>Status</th>}
        {assignee && <th>Assignee</th>}
        {streaming && <th>Streaming</th>}
        {details && <th>Details</th>}
        {action && <th className={actionClassName}>{actionLabel}</th>}
      </tr></thead>
      <tbody>{volumes.map((volume, index) => <tr key={volume.row_key ?? (volume as {id?: number}).id ?? index}>
        {project && <td className="cell-name truncate">{project(volume)}</td>}
        <td className="cell-name volume-name-cell" title={volume.name}>{volume.name || "—"}</td>
        <td className="mono-cell">{volume.file_format || "—"}</td>
        <td className="mono-cell">{formatShape(volume)}</td>
        <td className="mono-cell">{formatVoxelSize(volume)}</td>
        <td className="cell-badge"><RegionCoverage hasMask={Boolean(volume.has_region_mask)} coverage={volume.region_mask_coverage}/></td>
        <td className="cell-badge"><StatusBadge value={volume.label_type || "none"}/></td>
        {status && <td className="cell-badge">{status(volume)}</td>}
        {assignee && <td className="truncate">{assignee(volume)}</td>}
        {streaming && <td className="cell-badge cell-streaming">{streaming(volume)}</td>}
        {details && <td className="cell-details">{details(volume)}</td>}
        {action && <td className={`${actionClassName} compact-actions`}>
          <div className="volume-meta-action-content">{action(volume)}</div>
        </td>}
      </tr>)}</tbody>
    </table>
  </div>;
}
