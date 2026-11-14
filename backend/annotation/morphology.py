"""Morphology features of one object's binary (Z, Y, X) mask.

Used by stitching phase 6 (``stitch_measure``) and ``measure_volume``. The
skeleton is the same TEASAR skeleton the cable length comes from (one
skeletonisation per object); everything else is computed from the mask.

- **Width** does not use kimimaro's skeleton radius. It is twice the Euclidean
  distance from a skeleton point to the nearest background voxel, with the
  anisotropic voxel size applied: the Euclidean distance transform, evaluated
  only at skeleton points. See ``skeleton_radii_nm``.
- **Branch points / endpoints** are skeleton vertices of degree >= 3 / 1.
- **Tortuosity** is cable length / end-to-end distance of the longest path.
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
                     "sphericity")
# Skeleton points are voxel centres; the true centreline can sit up to half a
# voxel away. The radius is the best of the point and its half-voxel shifts.
# Skeleton points closer to a skeleton end than this many of their own radii
# are left out of the width statistics (see ``width_stats_nm``).
END_CAP_FACTOR = 1.5
_HALF_STEPS = np.array(list(itertools.product((0.0, -0.5, 0.5), repeat=3)))


@dataclass(frozen=True)
class Morphology:
    """Features of one object. ``None`` means not available: no skeleton (an
    object below the dust size) or no measurable value."""
    length_nm: float = 0.0
    width_mean_nm: float | None = None
    width_median_nm: float | None = None
    width_min_nm: float | None = None
    width_max_nm: float | None = None
    branch_points: int | None = None
    endpoints: int | None = None
    longest_path_nm: float | None = None
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
            "sphericity": text(self.sphericity),
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

def longest_path_nm(vertices_nm: np.ndarray, edges: np.ndarray) -> tuple[float, float]:
    """``(path length, end-to-end distance)`` of the longest shortest path in
    the skeleton, both in nm. Each connected piece is a tree, so its longest
    path is found by two sweeps (farthest vertex from any vertex, then farthest
    from that one); the longest over all pieces is returned."""
    n = len(vertices_nm)
    if n < 2 or not len(edges):
        return 0.0, 0.0
    lengths = np.linalg.norm(vertices_nm[edges[:, 0]] - vertices_nm[edges[:, 1]], axis=1)
    graph = coo_matrix((lengths, (edges[:, 0], edges[:, 1])), shape=(n, n)).tocsr()
    _, component = connected_components(graph, directed=False)
    starts = np.unique(component, return_index=True)[1]
    best = (0.0, 0.0)
    for row in dijkstra(graph, directed=False, indices=starts):
        first = int(np.argmax(np.where(np.isfinite(row), row, -1.0)))
        sweep = dijkstra(graph, directed=False, indices=first)
        second = int(np.argmax(np.where(np.isfinite(sweep), sweep, -1.0)))
        if sweep[second] > best[0]:
            best = (float(sweep[second]), float(np.linalg.norm(vertices_nm[first] - vertices_nm[second])))
    return best


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
    path, end_to_end = longest_path_nm(vertices, edges)
    return Morphology(
        length_nm=length, width_mean_nm=mean, width_median_nm=median, width_min_nm=low, width_max_nm=high,
        branch_points=int(np.count_nonzero(degree >= 3)), endpoints=int(np.count_nonzero(degree == 1)),
        longest_path_nm=path if end_to_end > 0 else None, end_to_end_nm=end_to_end if end_to_end > 0 else None,
        tortuosity=length / end_to_end if end_to_end > 0 else None, **shape)
