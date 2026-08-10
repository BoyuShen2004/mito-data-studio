import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { LabelLifecycleAction, LabelLifecycleState, LabelSummaryRow } from "../../api/viewer";
import { labelColorCss } from "./labelColor";
import { displayLayerRange } from "./layerIndex";
import { useVirtualRows } from "./useVirtualRows";

// Cellable-parity Labels panel — mirrors the "Filters Options" surface in
// cellable/labelme/app.py (~line 990: listFilterCombo / hideVerifiedCheckbox
// / solo+show-all buttons / sort buttons / verify+unverify buttons /
// labelStateStatsLabel), not just a thin id-filter box. Per
// progress/history/21-cellable-parity-followups.md: this replaced a v1
// version that only had search+solo+hide+delete — this round adds the
// state filter, hide-verified, sort, and lifecycle actions Cellable's user
// actually relies on.
export type LabelsScope = "slice" | "all";
type ShowFilter = "all" | "proposed" | "edited" | "verified" | "not_verified";
type SortMode = "id_asc" | "id_desc" | "size_asc" | "size_desc" | "state";

const STATE_DOT: Record<LabelLifecycleState, string> = {
  proposed: "○",
  edited: "◐",
  verified: "●",
};
const STATE_ORDER: Record<LabelLifecycleState, number> = { proposed: 0, edited: 1, verified: 2 };

/** Exact voxel count, visible on every row now (#31 item 1) — previously
 * only in a `title=` tooltip on "All" rows, not shown at all on "This
 * slice" rows. Compact `k` form above 10,000 (still exact below that,
 * where the digit count is short enough to just show outright). */
function formatVoxelCount(n: number): string {
  if (n >= 10000) return `${(n / 1000).toFixed(1)}k vox`;
  return `${n.toLocaleString()} vox`;
}

export default function LabelsPanel({
  scope,
  onScopeChange,
  activeId,
  onSetActiveId,
  sliceInstances,
  rows,
  rowsLoading,
  hiddenIds,
  soloId,
  onToggleHidden,
  onToggleSolo,
  onResetVisibility,
  pinnedIds,
  onTogglePinned,
  onPinMany,
  onClearPins,
  onJumpToZ,
  hideVerified,
  onHideVerifiedChange,
  hasRegionMask = false,
  hideOutsideRegion = false,
  onHideOutsideRegionChange,
  regionMemberIds = null,
  onLifecycleAction,
  onRefresh,
  readOnly = false,
  focusId = null,
  pinActiveToTopToken = 0,
}: {
  /** Which list the panel is showing. Controlled by `AnnotationCanvas` because
   * the canvas needs it too: picking a label with the Select tool follows the
   * same rule as clicking a row here — jump to the label in All, stay put in
   * This layer. */
  scope: LabelsScope;
  onScopeChange: (scope: LabelsScope) => void;
  activeId: number;
  onSetActiveId: (id: number) => void;
  sliceInstances: number[];
  rows: LabelSummaryRow[];
  rowsLoading: boolean;
  hiddenIds: Set<number>;
  soloId: number | null;
  onToggleHidden: (id: number) => void;
  onToggleSolo: (id: number) => void;
  onResetVisibility: () => void;
  pinnedIds: Set<number>;
  onTogglePinned: (id: number) => void;
  /** Pin many label ids into the 3D view at once (This slice / All). */
  onPinMany: (ids: number[]) => void;
  /** Remove every label from the shared 3D view. */
  onClearPins: () => void;
  onJumpToZ: (z: number) => void;
  hideVerified: boolean;
  onHideVerifiedChange: (v: boolean) => void;
  /** The volume has an ROI, so "Hide outside region" is meaningful at all. */
  hasRegionMask?: boolean;
  hideOutsideRegion?: boolean;
  onHideOutsideRegionChange?: (v: boolean) => void;
  /** Ids that touch the ROI *anywhere in z* — the same set Region only shows
   * whole. `null` means membership is unknown (no ROI, or the request has not
   * landed), and nothing is filtered. */
  regionMemberIds?: ReadonlySet<number> | null;
  onLifecycleAction: (labelId: number, action: LabelLifecycleAction) => void;
  onRefresh: () => void;
  /** View mode: hide verify/unverify; keep browse / visibility / 3D pin. */
  readOnly?: boolean;
  /** Scroll this label's row into view once when it first renders — the
   * public share page lands on one shared label that can be far down a long
   * list (03 item A). Purely a one-shot scroll: it never re-scrolls while the
   * recipient browses, and never changes selection or visibility. */
  focusId?: number | null;
  /**
   * Bumped when the user picks a label on the canvas (Select / View
   * eyedropper). Scrolls the active row to the **top** of the Labels list.
   * List-row clicks must not bump this — they only highlight in place.
   */
  pinActiveToTopToken?: number;
}) {
  const [filterText, setFilterText] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [showFilter, setShowFilter] = useState<ShowFilter>("all");
  const [sortMode, setSortMode] = useState<SortMode>("id_asc");
  const [rowMenu, setRowMenu] = useState<{
    id: number;
    state: LabelLifecycleState;
    x: number;
    y: number;
  } | null>(null);
  const rowMenuRef = useRef<HTMLDivElement | null>(null);

  const rowsById = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);

  const stats = useMemo(() => {
    const out = { total: rows.length, proposed: 0, edited: 0, verified: 0 };
    for (const r of rows) out[r.state] += 1;
    return out;
  }, [rows]);

  /** True only when this id is a *known* label that never reaches the ROI.
   *
   * An id the summary has never heard of is unsaved paint, not proof of
   * anything — hiding it would make a label vanish the moment it was drawn,
   * which is the opposite of what this filter is for. */
  const outsideRegion = useMemo(() => {
    if (!hideOutsideRegion || !regionMemberIds) return () => false;
    return (id: number) => rowsById.has(id) && !regionMemberIds.has(id);
  }, [hideOutsideRegion, regionMemberIds, rowsById]);

  const filteredSlice = useMemo(() => {
    const q = filterText.trim();
    const list = sliceInstances.filter((id) => {
      if (q && !String(id).includes(q)) return false;
      if (hideVerified && rowsById.get(id)?.state === "verified") return false;
      if (outsideRegion(id)) return false;
      return true;
    });
    return list;
  }, [sliceInstances, filterText, hideVerified, outsideRegion, rowsById]);

  const visibleAllRows = useMemo(() => {
    const q = filterText.trim();
    let list = rows.filter((r) => {
      if (q && !String(r.id).includes(q)) return false;
      if (hideVerified && r.state === "verified") return false;
      if (outsideRegion(r.id)) return false;
      if (showFilter === "all") return true;
      if (showFilter === "not_verified") return r.state !== "verified";
      return r.state === showFilter;
    });
    list = [...list].sort((a, b) => {
      switch (sortMode) {
        case "id_asc":
          return a.id - b.id;
        case "id_desc":
          return b.id - a.id;
        case "size_asc":
          return a.voxel_count - b.voxel_count;
        case "size_desc":
          return b.voxel_count - a.voxel_count;
        case "state":
          return STATE_ORDER[a.state] - STATE_ORDER[b.state] || a.id - b.id;
        default:
          return 0;
      }
    });
    return list;
  }, [rows, filterText, hideVerified, outsideRegion, showFilter, sortMode]);

  const activeRow = rowsById.get(activeId);

  useEffect(() => {
    if (!rowMenu) return;
    const close = (event: PointerEvent) => {
      if (!rowMenuRef.current?.contains(event.target as Node)) setRowMenu(null);
    };
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [rowMenu]);

  const openRowMenu = (
    event: React.MouseEvent,
    id: number,
    state: LabelLifecycleState = rowsById.get(id)?.state ?? "proposed",
  ) => {
    event.preventDefault();
    if (readOnly) return;
    setRowMenu({
      id,
      state,
      x: Math.max(8, Math.min(event.clientX, window.innerWidth - 170)),
      y: Math.max(8, Math.min(event.clientY, window.innerHeight - 180)),
    });
  };

  // "All" is one row per instance in the whole volume — thousands on a real EM
  // volume — so it renders windowed once it gets big (`useVirtualRows`). The
  // "This slice" list is bounded by what fits on one slice and stays plain.
  const panelRef = useRef<HTMLDivElement | null>(null);
  const allListRef = useRef<HTMLUListElement | null>(null);
  const virtual = useVirtualRows(visibleAllRows.length, panelRef, allListRef);
  const windowedAllRows = virtual.enabled
    ? visibleAllRows.slice(virtual.start, virtual.end)
    : visibleAllRows;

  const activeRowRef = useRef<HTMLLIElement | null>(null);
  const focusRowRef = useRef<HTMLLIElement | null>(null);
  const listMetaRef = useRef<HTMLDivElement | null>(null);

  /** Pin `id` to the first visible line below sticky "N of N / 3D all". */
  const scrollIdToTop = (id: number): boolean => {
    const index = scope === "slice"
      ? filteredSlice.indexOf(id)
      : visibleAllRows.findIndex((row) => row.id === id);
    if (index < 0) return false;
    const viewport = panelRef.current;
    if (!viewport) return false;
    const ceiling = listMetaRef.current?.offsetHeight ?? 0;

    if (scope === "all" && virtual.enabled) {
      virtual.scrollToIndex(index, { offsetTop: ceiling });
      return true;
    }

    if (viewport.clientHeight <= 0) return false;
    const rowEl =
      (id === activeId ? activeRowRef.current : null)
      ?? (id === focusId ? focusRowRef.current : null)
      ?? viewport.querySelector<HTMLElement>(`.labels-list li.labels-row-active`);
    if (!rowEl) return false;
    const vRect = viewport.getBoundingClientRect();
    const eRect = rowEl.getBoundingClientRect();
    viewport.scrollTop += eRect.top - (vRect.top + ceiling);
    return true;
  };

  // Canvas pick: bump `pinActiveToTopToken`. Keep retrying until the virtual
  // window includes the row — a single far jump from scrollTop=0 used to land
  // wrong until a second click (double-click) re-pinned after spacers settled.
  const lastPinTokenRef = useRef(0);
  const pendingPinIdRef = useRef<number | null>(null);
  useLayoutEffect(() => {
    if (pinActiveToTopToken > lastPinTokenRef.current) {
      lastPinTokenRef.current = pinActiveToTopToken;
      pendingPinIdRef.current = activeId;
    }
    const pendingId = pendingPinIdRef.current;
    if (pendingId == null) return;
    if (!scrollIdToTop(pendingId)) return;

    if (scope === "all" && virtual.enabled) {
      const index = visibleAllRows.findIndex((row) => row.id === pendingId);
      if (index < virtual.start || index >= virtual.end) return;
    }
    pendingPinIdRef.current = null;
  }, [
    pinActiveToTopToken,
    activeId,
    scope,
    filteredSlice,
    visibleAllRows,
    virtual.enabled,
    virtual.start,
    virtual.end,
    virtual.scrollToIndex,
  ]);

  // Hard-case / share open: one-shot pin (same math as canvas pick).
  const scrolledForRef = useRef<number | null>(null);
  useLayoutEffect(() => {
    if (focusId == null || scrolledForRef.current === focusId) return;
    if (!scrollIdToTop(focusId)) return;
    if (scope === "all" && virtual.enabled) {
      const index = visibleAllRows.findIndex((row) => row.id === focusId);
      if (index < virtual.start || index >= virtual.end) return;
    }
    scrolledForRef.current = focusId;
  }, [
    focusId,
    scope,
    filteredSlice,
    visibleAllRows,
    virtual.enabled,
    virtual.start,
    virtual.end,
    virtual.scrollToIndex,
    activeId,
  ]);

  const jumpToSearchMatch = () => {
    const id = Number(filterText.trim());
    if (!Number.isFinite(id)) return;
    const row = rowsById.get(id);
    if (row) {
      onSetActiveId(id);
      onJumpToZ(row.z_start);
    }
  };

  return (
    <div className="card labels-panel">
      <div className="labels-panel-chrome">
      <div className="row spread">
        <h3 style={{ margin: 0 }}>Labels</h3>
        <div className="row labels-header-actions">
          {/* Visibility only — clears solo/hidden. Left of Refresh so the
              recovery control is the first action when filters are active.
              Formerly labeled "Reset", which was mistaken for Reset labels. */}
          {(hiddenIds.size > 0 || soloId != null) && (
            <button
              className="secondary"
              title="Clear solo / hidden filters"
              onClick={onResetVisibility}
            >
              Show all
            </button>
          )}
          <button className="secondary" onClick={onRefresh}>Refresh</button>
        </div>
      </div>

      {/* Filters Options toggle + Hide Verified beside it (not buried in the
          dropdown) + state legend. Hide Verified defaults off so labels show. */}
      <div className="row spread labels-filters-header">
        <div className="row labels-filters-header-left">
          <button className="secondary" onClick={() => setFiltersOpen((v) => !v)}>
            Filters Options {filtersOpen ? "▲" : "▼"}
          </button>
          <label className="row labels-hide-verified" title="Hide Verified — H">
            <input
              type="checkbox"
              checked={hideVerified}
              onChange={(e) => onHideVerifiedChange(e.target.checked)}
            />
            Hide Verified
          </label>
          {/* Only meaningful on a volume that has an ROI at all. Independent of
              the Region only toggle — the list can be scoped to the ROI while
              the canvas still shows everything — but it hides exactly what
              Region only would hide: ids that never reach the region on any
              layer. */}
          {hasRegionMask && (
            <label
              className="row labels-hide-outside-region"
              title="Hide labels that never touch the region on any layer. Same membership as Region only; unsaved labels are never hidden."
            >
              <input
                type="checkbox"
                checked={hideOutsideRegion}
                disabled={!onHideOutsideRegionChange}
                onChange={(e) => onHideOutsideRegionChange?.(e.target.checked)}
              />
              Hide non-ROI
            </label>
          )}
        </div>
        <span className="muted labels-state-legend" title="○ Proposed  ◐ Edited  ● Verified">
          ○{stats.proposed} ◐{stats.edited} ●{stats.verified}
        </span>
      </div>

      {filtersOpen && (
        <div className="labels-filters-popup">
          <div className="row labels-filters-row">
            <span className="muted">Show:</span>
            <select value={showFilter} onChange={(e) => setShowFilter(e.target.value as ShowFilter)}>
              <option value="all">All</option>
              <option value="proposed">Proposed</option>
              <option value="edited">Edited</option>
              <option value="verified">Verified</option>
              <option value="not_verified">Not Verified</option>
            </select>
          </div>
          <div className="row labels-filters-row">
            <button className="secondary" title="Solo the active label — S" onClick={() => onToggleSolo(activeId)}>
              Solo
            </button>
            <button className="secondary" title="Show all labels — Shift+S" onClick={onResetVisibility}>
              Show All
            </button>
            {soloId != null && <span className="muted">Solo: {soloId}</span>}
          </div>
          <div className="row labels-filters-row">
            <button className="secondary" onClick={() => setSortMode("id_asc")}>
              ↑ ID
            </button>
            <button className="secondary" onClick={() => setSortMode("id_desc")}>
              ↓ ID
            </button>
            <button className="secondary" onClick={() => setSortMode("size_asc")}>
              ↑ Size
            </button>
            <button className="secondary" onClick={() => setSortMode("size_desc")}>
              ↓ Size
            </button>
            <button className="secondary" onClick={() => setSortMode("state")}>
              State
            </button>
          </div>
          {!readOnly && (
            <div className="row labels-filters-row">
              <button
                className="secondary"
                title="Verify the active label (a human has confirmed it's correct) — F"
                onClick={() => onLifecycleAction(activeId, "verify")}
              >
                ✓ Verify
              </button>
              <button
                className="secondary"
                title="Unverify the active label (move it back to Edited so it can be changed again)"
                disabled={activeRow?.state !== "verified"}
                onClick={() => onLifecycleAction(activeId, "unverify")}
              >
                ○ Unverify
              </button>
            </div>
          )}
        </div>
      )}

      <div className="tabs labels-scope-tabs">
        <button
          className={`tab ${scope === "slice" ? "tab-active" : ""}`}
          onClick={() => onScopeChange("slice")}
          title="Labels present on the current layer only"
        >
          This layer
        </button>
        <button
          className={`tab ${scope === "all" ? "tab-active" : ""}`}
          onClick={() => onScopeChange("all")}
          title="Every label in the whole volume's working copy"
        >
          All
        </button>
      </div>
      <input
        type="text"
        placeholder="Search label ID… (Enter to jump)"
        value={filterText}
        onChange={(e) => setFilterText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") jumpToSearchMatch();
        }}
        style={{ margin: "0.5rem 0" }}
      />
      </div>

      <div className="labels-list-scroll" ref={panelRef}>
      {/* Sticky list ceiling: canvas pin aligns rows to just below this, not
          under Filters / tabs / the panel title. */}
      <div className="row spread labels-list-meta" ref={listMetaRef}>
        <p className="muted labels-list-count">
          {scope === "slice"
            ? `${filteredSlice.length} label(s) on layer`
            : rowsLoading
              ? "Loading…"
              : `${visibleAllRows.length} of ${rows.length} label(s)`}
        </p>
        <span className="row labels-3d-bulk-actions">
        {scope === "slice" ? (
          <button
            type="button"
            className="secondary labels-3d-bulk"
            disabled={filteredSlice.length === 0}
            title="Show every label on this layer in the 3D view"
            onClick={() => onPinMany(filteredSlice)}
          >
            3D layer
          </button>
        ) : (
          <button
            type="button"
            className="secondary labels-3d-bulk"
            disabled={rowsLoading || visibleAllRows.length === 0}
            title="Show every listed label in the 3D view"
            onClick={() => onPinMany(visibleAllRows.map((r) => r.id))}
          >
            3D all
          </button>
        )}
          <button
            type="button"
            className="secondary labels-3d-bulk"
            disabled={pinnedIds.size === 0}
            title="Remove every label from the 3D view"
            onClick={onClearPins}
          >
            Clear 3D
          </button>
        </span>
      </div>
      {scope === "slice" ? (
        <>
          {sliceInstances.length === 0 && <p className="muted">No instances on this layer.</p>}
          {sliceInstances.length > 0 && filteredSlice.length === 0 && (
            <p className="muted">No instance matches "{filterText}".</p>
          )}
          <ul className="labels-list">
            {filteredSlice.map((id) => (
              <li
                key={id}
                onContextMenu={(event) => openRowMenu(event, id)}
                ref={id === activeId ? activeRowRef : id === focusId ? focusRowRef : undefined}
                className={`row spread${id === activeId ? " labels-row-active" : ""}`}
              >
                <span
                  className="row"
                  style={{ gap: 6, alignItems: "center" }}
                  onClick={() => onSetActiveId(id)}
                >
                  <Swatch id={id} />
                  {id}
                  <StateDot row={rowsById.get(id)} />
                  <span className="muted labels-row-size">
                    {rowsById.has(id) ? formatVoxelCount(rowsById.get(id)!.voxel_count) : "—"}
                  </span>
                </span>
                <span className="row" style={{ gap: 4 }}>
                  <LabelViewButtons
                    id={id}
                    pinnedIds={pinnedIds}
                    soloId={soloId}
                    hiddenIds={hiddenIds}
                    onTogglePinned={onTogglePinned}
                    onToggleSolo={onToggleSolo}
                    onToggleHidden={onToggleHidden}
                  />
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <>
          {!rowsLoading && rows.length === 0 && <p className="muted">Nothing painted in this volume yet.</p>}
          {rows.length > 0 && visibleAllRows.length === 0 && (
            <p className="muted">No label matches the current filters.</p>
          )}
          <ul
            className={`labels-list${virtual.enabled ? " labels-list-virtual" : ""}`}
            ref={allListRef}
          >
            {virtual.padTop > 0 && (
              <li aria-hidden="true" style={{ height: virtual.padTop, padding: 0 }} />
            )}
            {windowedAllRows.map((row) => (
              <li
                key={row.id}
                onContextMenu={(event) => openRowMenu(event, row.id, row.state)}
                data-row=""
                ref={row.id === activeId ? activeRowRef : row.id === focusId ? focusRowRef : undefined}
                className={`row spread${row.id === activeId ? " labels-row-active" : ""}`}
              >
                <span
                  className="row"
                  style={{ gap: 6, alignItems: "center", cursor: "pointer" }}
                  title={`${row.voxel_count} voxels · z ${displayLayerRange(row.z_start, row.z_end)} · ${row.state} (${row.origin})`}
                  onClick={() => {
                    onSetActiveId(row.id);
                    onJumpToZ(row.z_start);
                  }}
                >
                  <Swatch id={row.id} />
                  {row.id}
                  <StateDot row={row} />
                  <span className="muted labels-row-size">{formatVoxelCount(row.voxel_count)}</span>
                  <span className="muted" style={{ fontSize: "0.68rem" }}>
                    z{displayLayerRange(row.z_start, row.z_end)}
                  </span>
                </span>
                <span className="row" style={{ gap: 4 }}>
                  <LabelViewButtons
                    id={row.id}
                    pinnedIds={pinnedIds}
                    soloId={soloId}
                    hiddenIds={hiddenIds}
                    onTogglePinned={onTogglePinned}
                    onToggleSolo={onToggleSolo}
                    onToggleHidden={onToggleHidden}
                  />
                </span>
              </li>
            ))}
            {virtual.padBottom > 0 && (
              <li aria-hidden="true" style={{ height: virtual.padBottom, padding: 0 }} />
            )}
          </ul>
        </>
      )}
      </div>
      {rowMenu && !readOnly && (
        <div
          ref={rowMenuRef}
          className="canvas-context-menu labels-row-context-menu"
          role="menu"
          aria-label={`Label ${rowMenu.id} actions`}
          style={{ position: "fixed", left: rowMenu.x, top: rowMenu.y }}
        >
          <button
            type="button"
            className="secondary"
            disabled={rowMenu.state === "verified"}
            onClick={() => {
              onLifecycleAction(rowMenu.id, "verify");
              setRowMenu(null);
            }}
          >
            Verify
          </button>
          <button
            type="button"
            className="secondary"
            disabled={rowMenu.state !== "verified"}
            onClick={() => {
              onLifecycleAction(rowMenu.id, "unverify");
              setRowMenu(null);
            }}
          >
            Unverify
          </button>
        </div>
      )}
    </div>
  );
}

function Swatch({ id }: { id: number }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 12,
        height: 12,
        borderRadius: 3,
        background: labelColorCss(id),
        border: "1px solid var(--border)",
      }}
    />
  );
}

/** Shared 3D / Solo / Hide controls — same state on This slice and All, and
 * the same flags drive both the 2D canvas overlay and the 3D Labels fetch. */
function LabelViewButtons({
  id,
  pinnedIds,
  soloId,
  hiddenIds,
  onTogglePinned,
  onToggleSolo,
  onToggleHidden,
}: {
  id: number;
  pinnedIds: Set<number>;
  soloId: number | null;
  hiddenIds: Set<number>;
  onTogglePinned: (id: number) => void;
  onToggleSolo: (id: number) => void;
  onToggleHidden: (id: number) => void;
}) {
  const pinned = pinnedIds.has(id);
  const solo = soloId === id;
  const hidden = hiddenIds.has(id);
  return (
    <>
      <button
        type="button"
        className="secondary"
        title={pinned ? "Remove from 3D view" : "Show in 3D view"}
        onClick={() => onTogglePinned(id)}
        style={{ padding: "1px 6px", opacity: pinned ? 1 : 0.5 }}
      >
        3D
      </button>
      {/* Solo/hide change *visibility*; the 3D button is what decides which
          labels 3D loads at all. Keeping those separate is deliberate — see
          `AnnotationCanvas`'s `label3DIds` / `hidden3DIds`. */}
      <button
        type="button"
        className="secondary"
        title={
          solo
            ? "Un-solo"
            : "Solo — show only this label on the canvas (and, of the labels pinned to 3D, only this one)"
        }
        onClick={() => onToggleSolo(id)}
        style={{ padding: "1px 6px", opacity: solo ? 1 : 0.6 }}
      >
        {solo ? "◉" : "○"}
      </button>
      <button
        type="button"
        className="secondary"
        title={hidden ? "Show on canvas and in 3D" : "Hide on canvas and in 3D"}
        onClick={() => onToggleHidden(id)}
        style={{ padding: "1px 6px", opacity: hidden ? 0.4 : 1 }}
      >
        {hidden ? "🙈" : "👁"}
      </button>
    </>
  );
}

function StateDot({ row }: { row: LabelSummaryRow | undefined }) {
  if (!row) return null;
  return (
    <span className="muted" title={row.state}>
      {STATE_DOT[row.state]}
    </span>
  );
}
