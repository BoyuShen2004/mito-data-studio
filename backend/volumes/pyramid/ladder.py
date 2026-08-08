"""The mag ladder — which downsample levels exist, and by how much per axis.

Pure: no Django, no zarr, no filesystem. The maths is the part worth verifying
against hand-computed expectations, and it is verifiable only if it does not
need a database row and a volume on disk to run.

Axis order is ``(z, y, x)`` everywhere, matching the rest of the codebase.

**Anisotropy is the whole point.** Downsampling z at the same rate as x and y on
a 40 nm × 8 nm × 8 nm volume destroys z resolution five times faster than it
should: after two levels the z extent is 160 nm while xy is 32 nm, and the
"pyramid" is a stack of increasingly useless slabs. So a level doubles only the
axes whose *physical* voxel extent is currently smallest, driving the voxel
toward isotropy — see ADR-009 §5.
"""

from __future__ import annotations

from dataclasses import dataclass

#: An axis is eligible to double while its physical extent is within this factor
#: of the finest axis. Exactly 1.0 would make the ladder brittle against
#: floating-point voxel sizes (39.999 vs 40.0); a small tolerance keeps
#: near-equal axes doubling together, which is what the operator means.
ANISO_TOLERANCE = 1.5

#: Stop before any axis becomes smaller than this. A level three voxels tall is
#: not a useful view; it is a directory that costs a request.
MIN_MAG_EXTENT = 32

#: Hard ceiling, so a pathological voxel size cannot generate levels forever.
MAX_LEVELS = 16


@dataclass(frozen=True)
class MagLevel:
    """One level of the pyramid."""

    #: 0 for full resolution, then 1, 2, …
    level: int
    #: Per-axis downsample relative to full resolution, ``(fz, fy, fx)``.
    factors: tuple[int, int, int]
    #: Shape of this level's array, ``(z, y, x)``.
    shape: tuple[int, int, int]

    @property
    def name(self) -> str:
        """Array name on disk: the **xy** factor as a decimal string.

        ADR-009 §3 — doc 20 asks for "mags 1,2,4,8…", and xy is what a viewer
        means by resolution. The per-axis factors live in the array attributes,
        so anisotropy is not lost by naming.
        """
        return str(self.factors[2])

    @property
    def voxels(self) -> int:
        d, h, w = self.shape
        return d * h * w


def _downsampled_extent(size: int, factor: int) -> int:
    """Length of an axis after downsampling by ``factor``.

    Ceiling division: a 9-voxel axis at factor 2 yields 5, not 4 — the last
    output voxel is a partial block. Dropping it would silently truncate the
    volume, which is the kind of quiet data loss this project keeps refusing.
    """
    return max(1, -(-size // factor))


def build_ladder(
    shape: tuple[int, int, int],
    voxel_size: tuple[float, float, float] | None = None,
    *,
    max_levels: int = MAX_LEVELS,
    min_extent: int = MIN_MAG_EXTENT,
    tolerance: float = ANISO_TOLERANCE,
) -> list[MagLevel]:
    """Every level for ``shape``, finest first, starting at full resolution.

    ``voxel_size`` is ``(z, y, x)`` in physical units; ``None`` or a
    non-positive entry means "assume isotropic", which degenerates to the
    obvious ``(1,1,1) → (2,2,2) → …`` ladder.
    """
    if len(shape) != 3:
        raise ValueError("Pyramids are 3-D (z, y, x).")
    if any(s <= 0 for s in shape):
        raise ValueError(f"Shape has a non-positive axis: {shape}.")

    if voxel_size is None or any(v is None or v <= 0 for v in voxel_size):
        voxel = (1.0, 1.0, 1.0)
    else:
        voxel = (float(voxel_size[0]), float(voxel_size[1]), float(voxel_size[2]))

    levels = [MagLevel(level=0, factors=(1, 1, 1), shape=tuple(int(s) for s in shape))]

    factors = [1, 1, 1]
    for level in range(1, max_levels + 1):
        extents = [voxel[a] * factors[a] for a in range(3)]
        finest = min(extents)
        # Double only the axes that are still at (or near) the finest extent.
        doubling = [a for a in range(3) if extents[a] <= finest * tolerance]
        if not doubling:
            break

        candidate = list(factors)
        for a in doubling:
            candidate[a] *= 2

        candidate_shape = tuple(
            _downsampled_extent(int(shape[a]), candidate[a]) for a in range(3)
        )
        # Stop *before* adding a level that is too small to be useful. Only axes
        # actually being downsampled can shrink, so an already-thin z axis does
        # not veto further xy levels.
        if any(candidate_shape[a] < min_extent for a in doubling):
            break
        if candidate_shape == levels[-1].shape:
            break  # no progress; nothing left to gain

        factors = candidate
        levels.append(
            MagLevel(
                level=level,
                factors=(factors[0], factors[1], factors[2]),
                shape=candidate_shape,
            )
        )

    return levels


def relative_factors(child: MagLevel, parent: MagLevel) -> tuple[int, int, int]:
    """Per-axis factor to get from ``parent`` to ``child``.

    Levels are built from the level above rather than from full resolution, so
    the work per level is proportional to that level's size — the difference
    between a pyramid costing ~1.33× the base and costing *levels* × the base.
    """
    out = []
    for a in range(3):
        pf, cf = parent.factors[a], child.factors[a]
        if cf % pf != 0:
            raise ValueError(
                f"Level {child.level} factor {cf} is not a multiple of parent {pf}."
            )
        out.append(cf // pf)
    return (out[0], out[1], out[2])


def chunk_shape_for(
    shape: tuple[int, int, int], *, plane: int = 512
) -> tuple[int, int, int]:
    """Chunk shape for a level: one plane tile, ``(1, plane, plane)``, clipped.

    Slice-oriented rather than cubic, per ADR-009 §4: the access pattern this
    stack is built for is scrubbing through z, and a cubic chunk would fetch
    dozens of neighbouring planes that scrubbing never reads.
    """
    return (1, min(plane, int(shape[1])), min(plane, int(shape[2])))


def ladder_summary(levels: list[MagLevel]) -> dict:
    """Serialisable description, for group attributes and the job record."""
    return {
        "levels": [
            {
                "level": lv.level,
                "name": lv.name,
                "factors": list(lv.factors),
                "shape": list(lv.shape),
            }
            for lv in levels
        ],
        "count": len(levels),
    }
