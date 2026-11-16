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

from annotation.measurements import DUST_SIZE, TEASAR_CONST_NM, skeletonize_mask, validate_voxel_size
from annotation.third_party.em_erl_skel import cable_length

# Gaussian smoothing (in voxels) of the mask before marching cubes. The raw
# voxel staircase overestimates area by 5-19 % on anisotropic voxels; sigma 1
# brings spheres and cylinders within about 2 % (see test_morphology).
SURFACE_SIGMA = 1.0
MORPHOLOGY_FIELDS = ("width_mean_nm", "width_median_nm", "width_min_nm", "width_max_nm", "branch_points",
                     "endpoints", "longest_path_um", "end_to_end_um", "tortuosity", "surface_area_um2",
                     "sphericity", "length_smoothed_um", "longest_path_smoothed_um", "short_object",
                     "length_method")
# Smoothed length: moving average over this much path (nm), applied this many times.
SMOOTH_WINDOW_NM = 200.0
SMOOTH_PASSES = 2
# Skeleton points are voxel centres; the true centreline can sit up to half a
# voxel away. The radius is the best of the point and its half-voxel shifts.
# Skeleton points closer to a skeleton end than this many of their own radii
# are left out of the width statistics (see ``width_stats_nm``).
END_CAP_FACTOR = 1.5
# An object whose smoothed length is below this many times its largest width is flagged
# ``short_object``: about as wide as it is long, so its length is nearer a diameter (within
# 10 % on every test capsule and blob, 5 % above the threshold) and its tortuosity is 1.
SHORT_OBJECT_ASPECT = 1.5
# End caps (see ``cap_ends``). The last ``END_TRIM_FACTOR`` radii of path at a skeleton end are
# not trusted; the object's axis is taken from two cross-sections, there and
# ``END_DIRECTION_NM`` (at least one radius) further in, and the end is carried along it to
# the surface. 2 radii were chosen on 840 capsules (1.5 leaves part of the detour along Z) and
# checked on 480 others; see "Ends, by orientation" in docs/engineering/stitching.md.
END_TRIM_FACTOR = 2.0
END_DIRECTION_NM = 100.0
END_SHARE_ONE = 0.8         # of a branch that ends at a branch point: the most its end may replace
END_SECTION_FACTOR = 1.35   # a cross-section is the object within this many radii of the path
END_STEP_FACTOR = 1 / 3     # the centreline is followed in steps of this many radii (at least one voxel)
END_TIP_SHARE = 0.5         # following stops where the cross-section falls under this share of the widest
END_AXIS_ROUNDS = 3         # times the two cross-sections are re-cut square to the axis they give
RAY_STEP_NM = 1.0
# ``length_method``: how ``length_smoothed_um`` was measured (see ``cap_ends``).
METHOD_SKELETON, METHOD_CENTRE_CURVE = "skeleton", "centre_curve"
# Identifies how the numbers were made; written to measure_run.json, the logs and the delivery
# README. Raise the number whenever a definition or a parameter changes.
METHOD_VERSION = (f"morphology/5 (length: TEASAR cable; smoothed length: branch moving average "
                  f"{SMOOTH_WINDOW_NM:g} nm x {SMOOTH_PASSES}, ends carried to the surface along the local axis "
                  f"from {END_TRIM_FACTOR:g} radii in by following cross-section centres round bends, not at data "
                  f"edges, short unbranched objects along their centre curve without tortuosity; "
                  f"tortuosity: smoothed longest path / end-to-end; "
                  f"width: anisotropic EDT, tips within {END_CAP_FACTOR:g} radii left out; "
                  f"surface: marching cubes, sigma {SURFACE_SIGMA:g} voxel; "
                  f"short_object: smoothed length < {SHORT_OBJECT_ASPECT:g} x largest width)")
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
    length_method: str | None = None        # METHOD_SKELETON or METHOD_CENTRE_CURVE; None without a skeleton

    @property
    def short_object(self) -> bool | None:
        """True when the object is too short for its width for the length to be reliable;
        ``None`` without a skeleton width."""
        if self.width_max_nm is None:
            return None
        return self.length_smoothed_nm < SHORT_OBJECT_ASPECT * self.width_max_nm

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
            "short_object": "" if self.short_object is None else "true" if self.short_object else "false",
            "length_method": self.length_method or "",
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


# --- end caps ----------------------------------------------------------------

def _surface_along(mask: np.ndarray, voxel: np.ndarray, start: np.ndarray, direction: np.ndarray,
                   reach_nm: float) -> tuple[float, tuple[int, ...]] | None:
    """Distance (nm) from ``start`` along ``direction`` to where the ray leaves the object for
    the last time within ``reach_nm``, and the last voxel inside. ``None`` when ``start`` is
    outside or the ray is still inside at ``reach_nm``. A voxel owns the box around its centre,
    so the surface is a voxel face. The last exit, not the first: a dent in a rough surface
    can lie on the line before the tip does (on the real tile the first exit halved a few
    small objects)."""
    along = np.arange(0.0, reach_nm + RAY_STEP_NM, RAY_STEP_NM)
    index = np.rint((start + along[:, None] * direction) / voxel).astype(np.int64)
    in_array = ((index >= 0) & (index < mask.shape)).all(axis=1)
    inside = np.zeros(len(along), bool)
    inside[in_array] = mask[tuple(index[in_array].T)]
    if not inside[0] or inside[-1]:
        return None
    last_in = len(inside) - 1 - int(np.argmax(inside[::-1]))
    return float(along[last_in] + RAY_STEP_NM / 2), tuple(int(i) for i in index[last_in])


def _tip_along(mask: np.ndarray, voxel: np.ndarray, start: np.ndarray, direction: np.ndarray,
               reach_nm: float) -> tuple[float, tuple[int, ...]] | None:
    """``_surface_along``, with the distance averaged over a small bundle of parallel rays.

    One ray leaves the object at a voxel face, so its distance moves in steps of up to a voxel
    as the object shifts on the grid. Eight more rays a third of the largest voxel size to the
    sides cross other voxels and step at other places; their mean is smoother (not along Z,
    where every ray meets the same planes). On a rounded tip they stop about a nanometre
    earlier than the central one. The last voxel inside is the central ray's."""
    centre = _surface_along(mask, voxel, start, direction, reach_nm)
    if centre is None:
        return None
    side = np.cross(direction, np.eye(3)[int(np.argmin(np.abs(direction)))])
    side /= np.linalg.norm(side)
    other = np.cross(direction, side)
    spread = float(voxel.max()) / 3
    distances = [centre[0]]
    for i, j in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
        hit = _surface_along(mask, voxel, start + spread * (i * side + j * other), direction, reach_nm)
        if hit is not None:
            distances.append(hit[0])
    return float(np.mean(distances)), centre[1]


def _voxels_near(mask: np.ndarray, voxel: np.ndarray, centre: np.ndarray, radius_nm: float) -> np.ndarray:
    """Positions (nm, relative to ``centre``) of the object's voxels within ``radius_nm``."""
    low = np.maximum(np.floor((centre - radius_nm) / voxel).astype(int), 0)
    high = np.minimum(np.ceil((centre + radius_nm) / voxel).astype(int) + 1, mask.shape)
    if (high <= low).any():
        return np.zeros((0, 3))
    offsets = (np.argwhere(mask[tuple(slice(i, j) for i, j in zip(low, high))]) + low) * voxel - centre
    return offsets[np.einsum("ij,ij->i", offsets, offsets) <= radius_nm ** 2]


def _recentre(mask: np.ndarray, voxel: np.ndarray, point: np.ndarray, normal: np.ndarray,
              radius_nm: float) -> np.ndarray:
    """``point`` moved to the centroid of the object's cross-section through it: the voxels
    within ``END_SECTION_FACTOR`` radii of it in a slab one voxel thick across ``normal``. A
    skeleton point can sit a good part of a radius off the axis near an end; the centroid of
    a (possibly slanted) cut through a tube is on the axis."""
    return _section(mask, voxel, point, normal, radius_nm)[0]


def _section(mask: np.ndarray, voxel: np.ndarray, point: np.ndarray, normal: np.ndarray,
             radius_nm: float) -> tuple[np.ndarray, int]:
    """Centroid and voxel count of the cross-section through ``point`` (see ``_recentre``)."""
    near = _voxels_near(mask, voxel, point, END_SECTION_FACTOR * radius_nm)
    near = near[np.abs(near @ normal) <= float(voxel.max()) / 2]
    return (point + near.mean(axis=0) if len(near) else point), len(near)


def _follow(mask: np.ndarray, voxel: np.ndarray, start: np.ndarray, direction: np.ndarray, radius_nm: float,
            reach_nm: float) -> tuple[list[np.ndarray], tuple[int, ...]] | None:
    """The object's centreline from ``start`` to its surface, following bends: a list of points
    ending at the tip, and the last voxel inside. Each step goes ``END_STEP`` along the current
    direction, moves onto the centroid of the cross-section there (``_recentre``), and turns
    the direction toward the step just made. When the next step would leave the object, the
    tip is where the last direction leaves it (``_tip_along``). ``None`` when no tip is found
    within ``reach_nm`` of path."""
    step = max(float(voxel.max()), END_STEP_FACTOR * radius_nm)
    points, centre, tangent, travelled = [], np.asarray(start, float), np.asarray(direction, float), 0.0
    widest = _section(mask, voxel, centre, tangent, radius_nm)[1]
    while travelled < reach_nm:
        ahead = centre + step * tangent
        index = np.rint(ahead / voxel).astype(np.int64)
        if ((index < 0) | (index >= mask.shape)).any() or not mask[tuple(index)]:
            break
        section, count = _section(mask, voxel, ahead, tangent, radius_nm)
        widest = max(widest, count)
        if count < END_TIP_SHARE * widest:
            break       # inside the rounded tip: a few voxels' centroid is noise, the ray is not
        moved = section - centre
        length = float(np.linalg.norm(moved))
        if length < step / 4 or moved @ tangent <= 0:
            break
        centre = centre + moved
        tangent = tangent + moved / length
        tangent /= np.linalg.norm(tangent)
        points.append(centre)
        travelled += length
    hit = _tip_along(mask, voxel, centre, tangent, step + 2 * radius_nm + 3 * float(voxel.max()))
    if hit is None:
        return None
    return [*points, centre + hit[0] * tangent], hit[1]


def _spread(polyline: np.ndarray, fractions: np.ndarray) -> np.ndarray:
    """Points at the given fractions (0 = first point, 1 = last) of a polyline's length."""
    steps = np.linalg.norm(np.diff(polyline, axis=0), axis=1)
    along = np.concatenate([[0.0], np.cumsum(steps)])
    targets = np.clip(fractions, 0.0, 1.0) * along[-1]
    return np.stack([np.interp(targets, along, polyline[:, a]) for a in range(3)], axis=1)


def _in_boxes(index, boxes) -> bool:
    return any(all(box[2 * a] <= index[a] < box[2 * a + 1] for a in range(3)) for box in boxes)


def _principal_axis(mask: np.ndarray, voxel: np.ndarray):
    """``(centroid, unit axis)`` of the object's voxels: the axis of largest spread."""
    points = np.argwhere(mask) * voxel
    centroid = points.mean(axis=0)
    _, vectors = np.linalg.eigh(np.cov((points - centroid).T))
    return centroid, vectors[:, -1]


def cap_ends(mask: np.ndarray, voxel, vertices_nm: np.ndarray, edges: np.ndarray, radii: np.ndarray,
             *, cut_boxes=()) -> tuple[np.ndarray, bool]:
    """``vertices_nm`` (a smoothed skeleton) with every skeleton end moved to the object's tip,
    and whether the object was measured along its centre curve instead (below).

    A TEASAR skeleton does not end at the tip of a rounded end: it bends away from the axis
    inside the cap and stops at the surface beside the tip (68 nm off the axis and 24 nm short
    of the tip on a 120 nm capsule along X; along Z it leaves the axis earlier and can stop a
    whole radius short). The bend adds about as much path as the early stop removes, so the
    length is roughly right by accident for most orientations, but not along Z, and the
    end-to-end distance is always short.

    For each end, two cross-sections of the object are cut (``_recentre``): one
    ``END_TRIM_FACTOR`` radii of path from the end, one ``END_DIRECTION_NM`` (at least one
    radius) further in. The line through their centroids gives a point on the axis and a
    direction. From the inner one the object's centreline is followed to the surface
    (``_follow``), round any bend, and replaces the path from there to the end, its vertices
    spread along it, so the edges still apply.

    **Centre curve.** The object is measured along a curve followed both ways from the
    cross-section through its centroid, cut across its principal axis, when all of this
    holds: its skeleton is one unbranched path with two ends; after setting aside
    ``END_TRIM_FACTOR`` radii of path at each end (the radius being the largest inscribed
    radius on that end's stretch, as ``trim`` finds it), less than ``END_DIRECTION_NM`` or
    the path's largest radius, whichever is larger, is left between the two; the curve
    reaches the surface both ways; and neither end is cut off (below). In round numbers: an
    unbranched object whose smoothed skeleton is shorter than 2.5 times its largest width
    (4 radii + 100 nm when the radius is under 100 nm). The second return value says so; such
    an object gets no tortuosity.

    An end is left exactly as it was when its last ray leaves the object inside one of
    ``cut_boxes`` ((z0, z1, y0, y1, x0, x1) in mask voxels: the voxel layers where the data
    stops), or when the end itself is in one: the object is cut off there and has no tip.
    It is also left alone when no axis can be found (a branch of two or three points)."""
    voxel = np.asarray(voxel, float)
    vertices = np.asarray(vertices_nm, float)
    capped = vertices.copy()
    centre_curve = False
    edges = unique_edges(edges)
    degree = np.bincount(edges.ravel(), minlength=len(vertices))
    slack = 3 * float(voxel.max())

    def cut_off(end_point, last_inside) -> bool:
        end_voxel = tuple(int(i) for i in np.rint(end_point / voxel))
        return bool(cut_boxes) and (_in_boxes(last_inside, cut_boxes) or _in_boxes(end_voxel, cut_boxes))

    def trim(along, order, allowed):
        """``(cut, back, radius)``: where the replaced stretch starts, where the direction is
        taken from, and the object's radius there. The radius is the largest on the path out
        to ``back``: a skeleton end that sits beside the tip, or at the rim of a flat end, has
        small radii of its own for a long way in, and they say nothing about the width."""
        radius = float(radii[order[0]])
        while True:
            far_enough = np.flatnonzero(along[:allowed + 1] >= END_TRIM_FACTOR * radius)
            cut = int(far_enough[0]) if len(far_enough) else allowed
            back = min(int(np.searchsorted(along, along[cut] + max(END_DIRECTION_NM, radius), "left")),
                       len(order) - 1)
            widest = float(radii[order[:back + 1]].max())
            if widest <= radius:
                return cut, back, radius
            radius = widest

    branches = skeleton_branches(len(vertices), edges)
    for path in branches:
        if path[0] == path[-1]:
            continue
        ends = [degree[path[0]] == 1, degree[path[-1]] == 1]
        points = vertices[path]
        steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
        total = float(steps.sum())
        if all(ends):
            forward = np.concatenate([[0.0], np.cumsum(steps)])
            first = trim(forward, path, len(path) - 1)[0]
            second = trim(total - forward[::-1], path[::-1], len(path) - 1)[0]
            middle = forward[len(path) - 1 - second] - forward[first]
            if middle < max(END_DIRECTION_NM, radii[path].max()):
                # Short rod: its centre curve, followed both ways from the middle cross-section.
                if len(branches) != 1:
                    continue
                centroid, axis = _principal_axis(mask, voxel)
                if axis @ (points[-1] - points[0]) < 0:
                    axis = -axis
                reach = total + 2 * float(radii[path].max()) + slack
                widest = float(radii[path].max())
                middle_point = centroid
                for _ in range(END_AXIS_ROUNDS):
                    middle_point = _recentre(mask, voxel, middle_point, axis, widest)
                sides = [_follow(mask, voxel, middle_point, sign * axis, widest, reach) for sign in (-1.0, 1.0)]
                if None in sides or cut_off(points[0], sides[0][1]) or cut_off(points[-1], sides[1][1]):
                    continue
                curve = np.array([*sides[0][0][::-1], middle_point, *sides[1][0]])
                capped[path] = _spread(curve, np.linspace(0.0, 1.0, len(path)))
                centre_curve = True
                continue
        for is_end, order in zip(ends, (path, path[::-1])):
            if not is_end:
                continue
            pts = vertices[order]
            along = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
            allowed = len(order) - 1 if all(ends) else int(np.searchsorted(along, END_SHARE_ONE * total, "right")) - 1
            cut, back, radius = trim(along, order, allowed)
            if cut < 1 or back <= cut:
                continue
            rough = pts[cut] - pts[back]
            rough /= max(float(np.linalg.norm(rough)), 1e-12)
            # Each round moves the two points onto the axis and squares the cuts to it; a point
            # that starts far off the axis needs more than one. A branch point is not on the
            # branch's own axis, so it is used as it is.
            anchor, behind, direction = pts[cut], pts[back], rough
            for _ in range(END_AXIS_ROUNDS):
                anchor = _recentre(mask, voxel, anchor, direction, radius)
                if degree[order[back]] < 3:
                    behind = _recentre(mask, voxel, behind, direction, radius)
                norm = float(np.linalg.norm(anchor - behind))
                if norm <= 0:
                    break
                direction = (anchor - behind) / norm
            if norm <= 0:
                continue
            # From the inner cross-section the object's own centreline is followed to the tip.
            # The path's last stretch is replaced by it, vertices spread along it. It starts at
            # the path's own point, not at the centroid beside it: a skeleton keeps to voxel
            # centres, up to half a voxel off the axis, and a step sideways there would add
            # length.
            followed = _follow(mask, voxel, behind, direction, radius, along[back] + radius + slack)
            if followed is None or cut_off(pts[0], followed[1]):
                continue
            curve = np.array([*followed[0][::-1], pts[back]])
            capped[order[:back + 1]] = _spread(curve, along[:back + 1] / along[back])
    return capped, centre_curve


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

def measure_morphology(mask: np.ndarray, voxel_size_zyx, *, dust_size: int = DUST_SIZE,
                       cut_boxes=(), teasar_const_nm: float = TEASAR_CONST_NM) -> Morphology:
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
    skeleton = skeletonize_mask(mask, voxel, dust_size=dust_size, teasar_const_nm=teasar_const_nm)
    if skeleton is None or not len(skeleton.vertices):
        return Morphology(**shape)

    vertices = np.asarray(skeleton.vertices, float)        # nm, (Z, Y, X)
    edges = np.asarray(skeleton.edges, np.int64).reshape(-1, 2)
    length = float(cable_length(skeleton.vertices, skeleton.edges, [1, 1, 1]))
    degree = np.bincount(edges.ravel(), minlength=len(vertices))
    radii = skeleton_radii_nm(mask, vertices, voxel)
    mean, median, low, high = width_stats_nm(radii, vertices, edges)
    # One main path, chosen on the smoothed and end-capped skeleton and measured both ways.
    smoothed, centre_curve = cap_ends(mask, voxel, smooth_skeleton(vertices, edges), edges, radii,
                                       cut_boxes=cut_boxes)
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
        # No tortuosity for objects measured along their centre curve: they are at most about
        # 2.5 widths long, and the ratio would mostly be noise.
        tortuosity=main_smoothed / end_to_end if has_path and not centre_curve else None,
        length_method=METHOD_CENTRE_CURVE if centre_curve else METHOD_SKELETON, **shape)
