import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { fetchLabels3DMesh as authedFetchLabels3DMesh, type Labels3DMesh } from "../../api/viewer";
import { labelColor } from "./labelColor";

/** World-space size the longest axis of the loaded geometry is scaled to, so
 * the camera framing below is the same for a 40-voxel mito and a 4000-voxel
 * one. */
const WORLD_SPAN = 100;

/**
 * Many EM volumes (including our webknossos heart OME-TIFF) ship **no**
 * PhysicalSizeZ/Y/X. The mesh API then falls back to isotropic (1,1,1)
 * voxel spacing. A mito that is ~30 slices deep and ~800 px wide then
 * collapses to a paper-thin pancake after WORLD_SPAN normalisation
 * (30/800 ≈ 4% of the lateral span) — which is exactly the "thin layer"
 * look users report.
 *
 * Auto z-exaggeration is a *preview* convention: stretch z so the loaded
 * geometry's thickness is at least this fraction of its lateral extent.
 * (Manual 1×/2×/4×/8× multipliers were removed — always Auto.)
 */
const AUTO_MIN_Z_FRAC = 0.25;

/** Bounds for camera framing. A Solo target deliberately ignores every other
 * loaded mesh, including ones that are merely hidden rather than unloaded. */
export function framingBox(group: THREE.Group, labelId?: number | null): THREE.Box3 | null {
  const target = labelId != null
    ? group.children.find((child) => Number(child.name) === labelId)
    : group;
  if (!target) return null;
  const box = new THREE.Box3().setFromObject(target);
  return box.isEmpty() ? null : box;
}

/** Keep the browser's WebGL context restorable and expose lifecycle events to
 * React. Without preventDefault(), a lost context may be discarded forever. */
export function wireWebGLContextRecovery(
  canvas: HTMLCanvasElement,
  onLost: () => void,
  onRestored: () => void,
): () => void {
  const lost = (event: Event) => {
    event.preventDefault();
    onLost();
  };
  canvas.addEventListener("webglcontextlost", lost);
  canvas.addEventListener("webglcontextrestored", onRestored);
  return () => {
    canvas.removeEventListener("webglcontextlost", lost);
    canvas.removeEventListener("webglcontextrestored", onRestored);
  };
}

function effectiveVoxelZ(
  data: Labels3DMesh,
): { vz: number; vy: number; vx: number; boosted: boolean } {
  const [vz0, vy, vx] = data.voxelSize;
  const ez0 = data.size[0] * vz0;
  const ey = data.size[1] * vy;
  const ex = data.size[2] * vx;
  const lateral = Math.max(ex, ey, 1e-6);
  const target = lateral * AUTO_MIN_Z_FRAC;
  if (ez0 > 0 && ez0 < target) {
    return { vz: vz0 * (target / ez0), vy, vx, boosted: true };
  }
  return { vz: vz0, vy, vx, boosted: false };
}

// Cellable-parity 3D labels view — plays the role `VTKSurfaceWidget` plays
// locally (Qt + VTK marching-cubes iso-surfaces), reimplemented for the
// browser: the backend meshes each label with marching cubes
// (`cellable_port/labels_3d.py`) and this renders the resulting triangle
// surfaces.
//
// **What makes this rebuild** (deliberately narrow — item B3): the pinned
// label set (`labelIds`) and an explicit `refreshKey` bump. Nothing else.
// Hiding/solo-ing/filtering labels in the Labels list only toggles
// `mesh.visible` on geometry that is already here — no refetch, no re-mesh.
// Z-scale changes rebuild client-side from the cached mesh payload only.
export default function Labels3DPanel({
  taskId,
  labelIds,
  refreshKey,
  hiddenIds,
  focusLabelId,
  swapped,
  onToggleSwap,
  fetchMesh = authedFetchLabels3DMesh,
}: {
  taskId: number;
  /** The 3D pin set — the ONLY thing (besides `refreshKey`) that rebuilds. */
  labelIds: number[];
  refreshKey: number;
  /** Pinned labels to keep loaded but not draw (2D hide / Hide Verified). */
  hiddenIds?: Set<number>;
  /** Solo target to frame without rebuilding. Null keeps the current camera. */
  focusLabelId?: number | null;
  swapped?: boolean;
  onToggleSwap?: () => void;
  /** Injected so the public share page fetches via the token endpoint. */
  fetchMesh?: (taskId: number, labelIds: number[], signal?: AbortSignal) => Promise<Labels3DMesh>;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const groupRef = useRef<THREE.Group | null>(null);
  const frameGroupRef = useRef<(labelId?: number | null) => void>(() => undefined);
  const focusLabelIdRef = useRef(focusLabelId);
  focusLabelIdRef.current = focusLabelId;
  const hiddenIdsRef = useRef(hiddenIds);
  hiddenIdsRef.current = hiddenIds;
  const [phase, setPhase] = useState<"idle" | "fetching" | "building" | "empty" | "error">("empty");
  const [errorText, setErrorText] = useState<string | null>(null);
  const [stats, setStats] = useState<{ labels: number; triangles: number; truncated: number } | null>(
    null,
  );
  const [meshData, setMeshData] = useState<Labels3DMesh | null>(null);
  const [zBoosted, setZBoosted] = useState(false);
  const [webglLost, setWebglLost] = useState(false);
  const [rendererEpoch, setRendererEpoch] = useState(0);

  // Stable identity for the pin set: a re-render that produces an equal-but-
  // new array must not re-trigger the (expensive) load effect.
  const idsKey = useMemo(() => [...labelIds].sort((a, b) => a - b).join(","), [labelIds]);

  // Scene / camera / renderer — once per mount (layout so groupRef is ready
  // before the fetch effect below runs on the same commit).
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0b0d10);
    const camera = new THREE.PerspectiveCamera(45, 1, 0.5, 20000);
    camera.position.set(WORLD_SPAN * 1.5, WORLD_SPAN * 1.1, WORLD_SPAN * 1.5);
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch {
      setWebglLost(true);
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;

    // Three lights, not one: a surface lit from a single direction reads as a
    // flat silhouette. Key + fill from opposite sides + a sky/ground hemisphere
    // is what makes the curvature of a mitochondrion legible.
    const key = new THREE.DirectionalLight(0xffffff, 1.0);
    key.position.set(1, 1.4, 0.8);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0x93c5fd, 0.35);
    fill.position.set(-1, -0.6, -0.9);
    scene.add(fill);
    scene.add(new THREE.HemisphereLight(0xdbeafe, 0x0b0d10, 0.55));
    scene.add(new THREE.GridHelper(WORLD_SPAN * 2, 20, 0x334155, 0x1f2937));

    const group = new THREE.Group();
    scene.add(group);
    groupRef.current = group;

    /** Point the camera at one label, or at everything currently loaded. */
    frameGroupRef.current = (labelId) => {
      const box = framingBox(group, labelId);
      if (!box) return;
      const center = box.getCenter(new THREE.Vector3());
      const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1);
      const dist = radius / Math.sin((camera.fov * Math.PI) / 360);
      controls.target.copy(center);
      camera.position.copy(center).add(new THREE.Vector3(0.8, 0.6, 1).normalize().multiplyScalar(dist * 1.25));
      camera.near = Math.max(dist / 500, 0.1);
      camera.far = dist * 50;
      camera.updateProjectionMatrix();
      controls.update();
    };

    let frame = 0;
    let running = true;
    const resize = () => {
      const w = el.clientWidth || 1;
      const h = el.clientHeight || 1;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h, false);
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    const tick = () => {
      if (!running) return;
      frame = requestAnimationFrame(tick);
      controls.update();
      renderer.render(scene, camera);
    };
    const unwireContext = wireWebGLContextRecovery(
      renderer.domElement,
      () => {
        running = false;
        cancelAnimationFrame(frame);
        setWebglLost(true);
      },
      () => {
        setWebglLost(false);
        setRendererEpoch((value) => value + 1);
      },
    );
    tick();

    return () => {
      running = false;
      cancelAnimationFrame(frame);
      unwireContext();
      ro.disconnect();
      controls.dispose();
      renderer.dispose();
      frameGroupRef.current = () => undefined;
      groupRef.current = null;
      if (renderer.domElement.parentNode === el) el.removeChild(renderer.domElement);
    };
  }, [rendererEpoch]);

  // Fetch mesh payload. Z-scale does NOT belong here — changing it must not
  // re-hit the expensive server mesher.
  useEffect(() => {
    const ids = idsKey === "" ? [] : idsKey.split(",").map(Number);
    let cancelled = false;
    const controller = new AbortController();

    const load = async () => {
      if (ids.length === 0) {
        if (!cancelled) {
          setMeshData(null);
          setPhase("empty");
          setStats(null);
          setErrorText(null);
          setZBoosted(false);
        }
        return;
      }
      setPhase("fetching");
      setErrorText(null);
      try {
        const data = await fetchMesh(taskId, ids, controller.signal);
        if (cancelled) return;
        setMeshData(data);
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
        if (cancelled) return;
        setMeshData(null);
        setPhase("error");
        setErrorText(e instanceof Error ? e.message : "3D preview failed");
      }
    };
    void load();
    return () => {
      cancelled = true;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, idsKey, refreshKey]);

  // Build / rebuild Three.js meshes from the cached payload + current Z scale.
  useEffect(() => {
    const group = groupRef.current;
    if (!group) return;
    let cancelled = false;

    const clearGroup = () => {
      while (group.children.length) {
        const child = group.children.pop()!;
        group.remove(child);
        if (child instanceof THREE.Mesh) {
          child.geometry.dispose();
          const mat = child.material;
          if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
          else mat.dispose();
        }
      }
    };

    const yieldFrame = () => new Promise<void>((r) => requestAnimationFrame(() => r()));

    const addMeshes = async (data: Labels3DMesh) => {
      let triangles = 0;
      const spacing = effectiveVoxelZ(data);
      setZBoosted(spacing.boosted);
      const [oz, oy, ox] = data.origin;
      const { vz, vy, vx } = spacing;
      const ez = data.size[0] * vz;
      const ey = data.size[1] * vy;
      const ex = data.size[2] * vx;
      const scale = WORLD_SPAN / Math.max(ez, ey, ex, 1e-6);
      const buildPositions = (verts: Float32Array): Float32Array => {
        const out = new Float32Array(verts.length);
        for (let i = 0; i < verts.length; i += 3) {
          out[i] = ((verts[i + 2] - ox) * vx - ex / 2) * scale;
          out[i + 1] = -(((verts[i + 1] - oy) * vy - ey / 2) * scale);
          out[i + 2] = ((verts[i] - oz) * vz - ez / 2) * scale;
        }
        return out;
      };
      for (let i = 0; i < data.meshes.length; i++) {
        if (cancelled) return { labels: 0, triangles: 0 };
        const m = data.meshes[i];
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.BufferAttribute(buildPositions(m.vertices), 3));
        geometry.setIndex(new THREE.BufferAttribute(m.indices, 1));
        geometry.computeVertexNormals();
        const [r, g, b] = labelColor(m.id);
        const material = new THREE.MeshStandardMaterial({
          color: new THREE.Color(r / 255, g / 255, b / 255),
          roughness: 0.55,
          metalness: 0.05,
          side: THREE.DoubleSide,
        });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.name = String(m.id);
        mesh.visible = !hiddenIdsRef.current?.has(m.id);
        group.add(mesh);
        triangles += m.indices.length / 3;
        if (i % 4 === 3) await yieldFrame();
      }
      return { labels: data.meshes.length, triangles };
    };

    const build = async () => {
      if (!meshData) {
        clearGroup();
        return;
      }
      if (meshData.meshes.length === 0) {
        clearGroup();
        setPhase("empty");
        setStats(null);
        setZBoosted(false);
        return;
      }
      setPhase("building");
      clearGroup();
      const built = await addMeshes(meshData);
      if (cancelled) return;
      setStats({ ...built, truncated: meshData.truncated });
      setPhase("idle");
      frameGroupRef.current(focusLabelIdRef.current);
    };
    void build();
    return () => {
      cancelled = true;
    };
  }, [meshData, rendererEpoch]);

  // Visibility only — hiding a pinned label in the Labels list must not cost
  // a refetch or a re-mesh (item B3).
  useEffect(() => {
    const group = groupRef.current;
    if (!group) return;
    for (const child of group.children) {
      child.visible = !hiddenIds?.has(Number(child.name));
    }
  }, [hiddenIds, stats]);

  // Entering or changing Solo is a camera action as well as a visibility
  // action. Frame the already-loaded mesh directly: no refetch or remesh.
  // Clearing Solo intentionally keeps the current camera to avoid a jarring
  // jump back to the multi-label overview.
  useEffect(() => {
    if (focusLabelId == null || focusLabelId < 1) return;
    frameGroupRef.current(focusLabelId);
  }, [focusLabelId, stats]);

  const statusText =
    webglLost
      ? "3D renderer lost — Retry"
      : phase === "fetching"
      ? "Meshing on server…"
      : phase === "building"
        ? "Building surfaces…"
        : phase === "empty"
          ? "No labels pinned"
          : phase === "error"
            ? errorText ?? "3D preview failed"
            : stats
              ? `${stats.labels} label(s) · ${stats.triangles.toLocaleString()} tris` +
                (stats.truncated ? ` · ${stats.truncated} not shown` : "") +
                (zBoosted ? " · z↑" : "")
              : "No labels pinned";

  return (
    <div className="card labels-3d-panel">
      <div className="row spread labels-3d-header">
        <h3 style={{ margin: 0 }}>3D Labels</h3>
        <span className={`muted labels-3d-status${phase === "error" || webglLost ? " labels-3d-status-error" : ""}`}>
          {statusText}
        </span>
        {webglLost && (
          <button
            type="button"
            className="secondary"
            onClick={() => {
              setWebglLost(false);
              setRendererEpoch((value) => value + 1);
            }}
          >
            Retry
          </button>
        )}
        {onToggleSwap && (
          <button
            type="button"
            className="secondary labels-3d-swap"
            title={
              swapped
                ? "Swap back — restore the editable 2D canvas to the center"
                : "Swap — enlarge 3D (2D editing pauses until you swap back)"
            }
            onClick={onToggleSwap}
          >
            Swap
          </button>
        )}
      </div>
      <div ref={containerRef} className="labels-3d-view" />
    </div>
  );
}
