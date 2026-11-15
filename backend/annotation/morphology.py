"""Morphology features of one object's binary (Z, Y, X) mask.

Used by stitching phase 6 (``stitch_measure``) and ``measure_volume``. The
skeleton is the same TEASAR skeleton the cable length comes from (one
skeletonisation per object); everything else is computed from the mask.

- **Width** does not use kimimaro's skeleton radius. It is twice the Euclidean
  distance from a skeleton point to the nearest background voxel, with the
  anisotropic voxel size applied: the Euclidean distance transform, evaluated
  only at skeleton points. See ``skeleton_radii_nm``.
- **Smoothed length** is the cable length of the same skeleton after each
  branch is smoothed along its path. The raw cable length follows voxel
  centres, so it zigzags and staircases; see ``smoothed_cable_length_nm``.
- **Branch points / endpoints** are skeleton vertices of degree >= 3 / 1.
- **Tortuosity** is the smoothed length of the longest path through the
  skeleton / the straight distance between that path's two ends. Branches off
  the path do not count.
- **Surface area** is the marching-cubes surface of the lightly smoothed mask.
- **Sphericity** is ``pi^(1/3) (6 V)^(2/3) / A`` (1 for a sphere).

Voxel size is in nm, (Z, Y, X), and is applied exactly once here. The part of
the object inside the mask array is measured as a closed solid: everything
outside the array counts as background. The definitions and their limits are
in ``docs/engineering/stitching.md`` ("Morphology").
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np
import scipy.ndimage as ndi
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree

from annotation.measurements import DUST_SIZE, skeletonize_mask, validate_voxel_size
from annotation.third_party.em_erl_skel import cable_length

# Gaussian smoothing (in voxels) of the mask before marching cubes. The raw
# voxel staircase overestimates area by 5-19 % on anisotropic voxels; sigma 1
# brings spheres and cylinders within about 2 % (see test_morphology).
SURFACE_SIGMA = 1.0
MORPHOLOGY_FIELDS = ("width_mean_nm", "width_median_nm", "width_min_nm", "width_max_nm", "branch_points",
                     "endpoints", "longest_path_um", "end_to_end_um", "tortuosity", "surface_area_um2",
                     "sphericity", "length_smoothed_um", "longest_path_smoothed_um")
# Smoothed length: moving average over this much path (nm), applied this many times.
SMOOTH_WINDOW_NM = 200.0
SMOOTH_PASSES = 2
# Skeleton points are voxel centres; the true centreline can sit up to half a
# voxel away. The radius is the best of the point and its half-voxel shifts.
# Skeleton points closer to a skeleton end than this many of their own radii
# are left out of the width statistics (see ``width_stats_nm``).
END_CAP_FACTOR = 1.5
# Identifies how the numbers were made; written to measure_run.json, the logs and the delivery
# README. Raise the number whenever a definition or a parameter changes.
METHOD_VERSION = (f"morphology/2 (length: TEASAR cable; smoothed length: branch moving average "
                  f"{SMOOTH_WINDOW_NM:g} nm x {SMOOTH_PASSES}; tortuosity: smoothed longest path / end-to-end; "
                  f"width: anisotropic EDT, tips within {END_CAP_FACTOR:g} radii left out; "
                  f"surface: marching cubes, sigma {SURFACE_SIGMA:g} voxel)")
_HALF_STEPS = np.array(list(itertools.product((0.0, -0.5, 0.5), repeat=3)))


@dataclass(frozen=True)
class Morphology:
    """Features of one object. ``None`` means not available: no skeleton (an
    object below the dust size) or no measurable value."""
    length_nm: float = 0.0
    length_smoothed_nm: float = 0.0
    width_mean_nm: float | None = None
    width_median_nm: float | None = None
    width_min_nm: float | None = None
    width_max_nm: float | None = None
    branch_points: int | None = None
    endpoints: int | None = None
    longest_path_nm: float | None = None
    longest_path_smoothed_nm: float | None = None
    end_to_end_nm: float | None = None
    tortuosity: float | None = None
    surface_area_nm2: float | None = None
    sphericity: float | None = None

    def row(self) -> dict[str, str]:
        """The ``MORPHOLOGY_FIELDS`` columns (empty where not available)."""
        def text(value, scale=1.0):
            return "" if value is None else repr(value / scale) if isinstance(value, float) else str(value)

        return {
            "width_mean_nm": text(self.width_mean_nm), "width_median_nm": text(self.width_median_nm),
            "width_min_nm": text(self.width_min_nm), "width_max_nm": text(self.width_max_nm),
            "branch_points": text(self.branch_points), "endpoints": text(self.endpoints),
            "longest_path_um": text(self.longest_path_nm, 1e3), "end_to_end_um": text(self.end_to_end_nm, 1e3),
            "tortuosity": text(self.tortuosity), "surface_area_um2": text(self.surface_area_nm2, 1e6),
            "sphericity": text(self.sphericity), "length_smoothed_um": text(self.length_smoothed_nm, 1e3),
            "longest_path_smoothed_um": text(self.longest_path_smoothed_nm, 1e3),
        }


EMPTY_ROW = dict.fromkeys(MORPHOLOGY_FIELDS, "")


# --- width -------------------------------------------------------------------

def background_shell_nm(mask: np.ndarray, voxel) -> np.ndarray:
    """Centres (nm, array coordinates) of the background voxels that share a
    face with the object, including the layer just outside the array. The
    nearest background voxel to any point inside the object is one of them."""
    padded = np.pad(mask, 1)
    shell = np.zeros(padded.shape, bool)
    for axis in range(3):
        lower = [slice(None)] * 3
        upper = [slice(None)] * 3
        lower[axis], upper[axis] = slice(None, -1), slice(1, None)
        shell[tuple(lower)] |= padded[tuple(upper)]
        shell[tuple(upper)] |= padded[tuple(lower)]
    shell &= ~padded
    return (np.argwhere(shell) - 1) * np.asarray(voxel, float)


def skeleton_radii_nm(mask: np.ndarray, vertices_nm: np.ndarray, voxel, *, refine: bool = True) -> np.ndarray:
    """Inscribed-sphere radius (nm) at each skeleton vertex: the distance to
    the nearest background voxel centre, voxel size applied.

    With ``refine=False`` this is exactly the anisotropic Euclidean distance
    transform of the mask sampled at the vertices. With ``refine`` the radius
    is the largest over the vertex and its half-voxel shifts, because a
    skeleton voxel can be half a voxel off the true centreline (always, when
    the object is an even number of voxels across), which would read up to
    half a voxel (15 nm in Z at 30 nm) too small."""
    tree = cKDTree(background_shell_nm(mask, voxel))
    points = np.asarray(vertices_nm, float)
    if not refine:
        return tree.query(points)[0]
    shifted = points[:, None, :] + _HALF_STEPS[None, :, :] * np.asarray(voxel, float)
    return tree.query(shifted.reshape(-1, 3))[0].reshape(len(points), -1).max(axis=1)


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values, kind="stable")
    cumulative = np.cumsum(weights[order])
    return float(values[order][np.searchsorted(cumulative, cumulative[-1] / 2.0)])


def width_stats_nm(radii: np.ndarray, vertices_nm: np.ndarray, edges: np.ndarray) -> tuple[float, float, float, float]:
    """``(mean, median, min, max)`` width (2 x radius) along the skeleton.

    Vertices closer to a skeleton endpoint than ``END_CAP_FACTOR`` times
    their own radius are left out: there the inscribed sphere is limited by
    the object's end, not its sides, so the value measures the distance to
    the tip (in a rounded tip the radius equals that distance, hence a factor
    above 1). When that removes every vertex (a blob about as long as it is
    wide) all vertices are used. The maximum is always over all vertices: the
    largest inscribed sphere is never a tip effect, and for a blob it is the
    only meaningful width. Mean
    and median are weighted by the cable each vertex stands for (half of each
    edge at it), so they do not depend on how densely the path is sampled:
    vertices are one per voxel, 30 nm apart along Z but 8 nm along X."""
    degree = np.bincount(edges.ravel(), minlength=len(vertices_nm))
    weights = np.zeros(len(vertices_nm))
    if len(edges):
        lengths = np.linalg.norm(vertices_nm[edges[:, 0]] - vertices_nm[edges[:, 1]], axis=1)
        np.add.at(weights, edges[:, 0], lengths / 2)
        np.add.at(weights, edges[:, 1], lengths / 2)
    keep = np.ones(len(vertices_nm), bool)
    ends = vertices_nm[degree == 1]
    if len(ends):
        keep = cKDTree(ends).query(vertices_nm)[0] > END_CAP_FACTOR * radii
        if not keep.any():
            keep[:] = True
    widths, weights = 2.0 * radii[keep], weights[keep]
    if weights.sum() <= 0:
        weights = np.ones(len(widths))
    return (float(np.average(widths, weights=weights)), _weighted_median(widths, weights),
            float(widths.min()), float(2.0 * radii.max()))


# --- skeleton graph ----------------------------------------------------------

def longest_path(vertices_nm: np.ndarray, edges: np.ndarray) -> list[int]:
    """Vertex indices, in order, of the longest shortest path in the skeleton.
    Each connected piece is a tree, so its longest path is found by two sweeps
    (farthest vertex from any vertex, then farthest from that one); the longest
    over all pieces is returned. Empty when there is no edge."""
    n = len(vertices_nm)
    if n < 2 or not len(edges):
        return []
    lengths = np.linalg.norm(vertices_nm[edges[:, 0]] - vertices_nm[edges[:, 1]], axis=1)
    graph = coo_matrix((lengths, (edges[:, 0], edges[:, 1])), shape=(n, n)).tocsr()
    _, component = connected_components(graph, directed=False)
    starts = np.unique(component, return_index=True)[1]
    best, best_length = [], -1.0
    for row in np.atleast_2d(dijkstra(graph, directed=False, indices=starts)):
        first = int(np.argmax(np.where(np.isfinite(row), row, -1.0)))
        sweep, before = dijkstra(graph, directed=False, indices=first, return_predecessors=True)
        last = int(np.argmax(np.where(np.isfinite(sweep), sweep, -1.0)))
        if sweep[last] > best_length:
            best_length, best = float(sweep[last]), [last]
            while best[-1] != first:
                best.append(int(before[best[-1]]))
    return best[::-1]


def path_length_nm(points: np.ndarray) -> float:
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum()) if len(points) > 1 else 0.0


def longest_path_nm(vertices_nm: np.ndarray, edges: np.ndarray) -> tuple[float, float]:
    """``(path length, end-to-end distance)`` of ``longest_path``, both in nm."""
    path = longest_path(vertices_nm, edges)
    if not path:
        return 0.0, 0.0
    return path_length_nm(vertices_nm[path]), float(np.linalg.norm(vertices_nm[path[0]] - vertices_nm[path[-1]]))


# --- smoothed length ---------------------------------------------------------

def unique_edges(edges: np.ndarray) -> np.ndarray:
    """``edges`` with each undirected edge once and self-loops removed."""
    edges = np.sort(np.asarray(edges, np.int64).reshape(-1, 2), axis=1)
    return np.unique(edges[edges[:, 0] != edges[:, 1]], axis=0).reshape(-1, 2)


def cable_nm(vertices_nm: np.ndarray, edges: np.ndarray) -> float:
    """Sum of edge lengths (nm), each undirected edge once."""
    edges = unique_edges(edges)
    return float(np.linalg.norm(vertices_nm[edges[:, 0]] - vertices_nm[edges[:, 1]], axis=1).sum())


def skeleton_branches(n_vertices: int, edges: np.ndarray) -> list[list[int]]:
    """The skeleton as ordered vertex paths. Each path runs between two nodes
    (vertices of degree other than 2: endpoints and branch points) through
    degree-2 vertices only; every edge is in exactly one path (repeated edges and self-loops are
    ignored). A closed loop
    without a node is one path that starts and ends at the same vertex."""
    neighbours: list[list[int]] = [[] for _ in range(n_vertices)]
    # A repeated edge would make a vertex look like part of a chain and the walk below
    # bounce between its two ends for ever, so each undirected edge is taken once.
    for a, b in unique_edges(edges).tolist():
        neighbours[a].append(b)
        neighbours[b].append(a)
    used: set[tuple[int, int]] = set()

    def walk(start: int, nxt: int) -> list[int]:
        path = [start, nxt]
        used.update(((start, nxt), (nxt, start)))
        while len(neighbours[path[-1]]) == 2 and path[-1] != start:
            a, b = neighbours[path[-1]]
            step = b if a == path[-2] else a
            used.update(((path[-1], step), (step, path[-1])))
            path.append(step)
        return path

    paths = []
    for node in range(n_vertices):
        if len(neighbours[node]) != 2:
            paths += [walk(node, nxt) for nxt in neighbours[node] if (node, nxt) not in used]
    for vertex in range(n_vertices):        # what is left are loops of degree-2 vertices
        paths += [walk(vertex, nxt) for nxt in neighbours[vertex] if (vertex, nxt) not in used]
    return paths


def smooth_path(points: np.ndarray, window_nm: float) -> np.ndarray:
    """``points`` (an ordered path, nm) with every point replaced by the mean
    of the path within ``window_nm / 2`` of it along the path, weighted by the
    cable each point stands for. The window shrinks toward the two ends, so
    the first and last point do not move."""
    if len(points) < 3:
        return points
    steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    along = np.concatenate([[0.0], np.cumsum(steps)])
    weights = np.zeros(len(points))
    weights[:-1] += steps / 2
    weights[1:] += steps / 2
    total_weight = np.concatenate([[0.0], np.cumsum(weights)])
    total_points = np.vstack([np.zeros(3), np.cumsum(points * weights[:, None], axis=0)])
    half = np.minimum(window_nm / 2, np.minimum(along, along[-1] - along))
    first = np.searchsorted(along, along - half, "left")
    last = np.searchsorted(along, along + half, "right")
    smoothed = (total_points[last] - total_points[first]) / np.maximum(total_weight[last] - total_weight[first],
                                                                       1e-12)[:, None]
    smoothed[0], smoothed[-1] = points[0], points[-1]
    return smoothed


def smooth_skeleton(vertices_nm: np.ndarray, edges: np.ndarray, *, window_nm: float = SMOOTH_WINDOW_NM,
                    passes: int = SMOOTH_PASSES) -> np.ndarray:
    """The skeleton's vertices after smoothing each branch along its path.

    A TEASAR skeleton is a chain of voxel centres. Its raw length is too long
    wherever the true centreline is not a row of voxels: it hops between two
    equally central rows (30 nm per hop in Z), and it climbs oblique or curved
    stretches in voxel steps. On (30, 8, 8) nm voxels that reads 6-40 % long
    on test tubes, and 170 % on a cylinder centred between two Z planes.

    Each branch is smoothed with ``smooth_path`` ``passes`` times; endpoints
    and branch points stay where they are, so the topology and the ends are
    unchanged (the edges still apply). Bends tighter than about 2.5 tube radii
    are cut slightly (2-6 % short)."""
    vertices = np.asarray(vertices_nm, float)
    smoothed = vertices.copy()
    for path in skeleton_branches(len(vertices), np.asarray(edges, np.int64).reshape(-1, 2)):
        points = vertices[path]
        for _ in range(passes):
            points = smooth_path(points, window_nm)
        smoothed[path[1:-1]] = points[1:-1]
    return smoothed


def smoothed_cable_length_nm(vertices_nm: np.ndarray, edges: np.ndarray, *, window_nm: float = SMOOTH_WINDOW_NM,
                             passes: int = SMOOTH_PASSES) -> float:
    """Cable length (nm) of the skeleton after ``smooth_skeleton``."""
    return cable_nm(smooth_skeleton(vertices_nm, edges, window_nm=window_nm, passes=passes), edges)


# --- surface -----------------------------------------------------------------

def surface_area_nm2(mask: np.ndarray, voxel, *, sigma: float = SURFACE_SIGMA) -> float | None:
    """Area (nm^2) of the marching-cubes surface of the mask, smoothed with a
    Gaussian of ``sigma`` voxels and cut at 0.5. An object too thin to survive
    the smoothing is meshed unsmoothed instead. ``None`` for an empty mask."""
    from skimage.measure import marching_cubes, mesh_surface_area

    if not mask.any():
        return None
    box = ndi.find_objects(mask.astype(np.uint8))[0]
    solid = np.pad(mask[box], 4).astype(np.float32)
    smooth = ndi.gaussian_filter(solid, sigma) if sigma else solid
    if smooth.max() <= 0.5:
        smooth = solid
    vertices, faces, _, _ = marching_cubes(smooth, 0.5, spacing=tuple(float(v) for v in voxel))
    return float(mesh_surface_area(vertices, faces))


def sphericity(volume_nm3: float, area_nm2: float) -> float:
    """Wadell sphericity: the surface of a sphere of the same volume over the
    object's surface. 1 for a sphere, lower for anything else."""
    return math.pi ** (1 / 3) * (6.0 * volume_nm3) ** (2 / 3) / area_nm2


# --- entry point -------------------------------------------------------------

def measure_morphology(mask: np.ndarray, voxel_size_zyx, *, dust_size: int = DUST_SIZE) -> Morphology:
    """Cable length and morphology of a binary (Z, Y, X) mask.

    ``length_nm`` is exactly ``measurements.skeleton_length_nm`` of the same
    mask (same skeleton). Voxel size is passed once: the skeleton's vertices
    are already in nm."""
    voxel = validate_voxel_size(voxel_size_zyx)
    mask = np.asarray(mask) > 0
    count = int(np.count_nonzero(mask))
    if not count:
        return Morphology()
    area = surface_area_nm2(mask, voxel)
    shape = {"surface_area_nm2": area,
             "sphericity": sphericity(count * voxel[0] * voxel[1] * voxel[2], area) if area else None}
    skeleton = skeletonize_mask(mask, voxel, dust_size=dust_size)
    if skeleton is None or not len(skeleton.vertices):
        return Morphology(**shape)

    vertices = np.asarray(skeleton.vertices, float)        # nm, (Z, Y, X)
    edges = np.asarray(skeleton.edges, np.int64).reshape(-1, 2)
    length = float(cable_length(skeleton.vertices, skeleton.edges, [1, 1, 1]))
    degree = np.bincount(edges.ravel(), minlength=len(vertices))
    mean, median, low, high = width_stats_nm(skeleton_radii_nm(mask, vertices, voxel), vertices, edges)
    # One main path, chosen on the smoothed skeleton and measured both ways.
    smoothed = smooth_skeleton(vertices, edges)
    main = longest_path(smoothed, edges)
    end_to_end = float(np.linalg.norm(smoothed[main[0]] - smoothed[main[-1]])) if main else 0.0
    has_path = end_to_end > 0
    main_smoothed = path_length_nm(smoothed[main])
    return Morphology(
        length_nm=length,
        length_smoothed_nm=cable_nm(smoothed, edges),
        width_mean_nm=mean, width_median_nm=median, width_min_nm=low, width_max_nm=high,
        branch_points=int(np.count_nonzero(degree >= 3)), endpoints=int(np.count_nonzero(degree == 1)),
        longest_path_nm=path_length_nm(vertices[main]) if has_path else None,
        longest_path_smoothed_nm=main_smoothed if has_path else None,
        end_to_end_nm=end_to_end if has_path else None,
        tortuosity=main_smoothed / end_to_end if has_path else None, **shape)
