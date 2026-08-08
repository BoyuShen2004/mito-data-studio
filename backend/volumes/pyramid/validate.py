"""Random chunk checksum validation (doc 20 §Pyramid job).

Doc 20 asks the build to "validate random chunk checksums" before a volume is
marked ready. The check is worth stating precisely, because a weak version of it
would pass on a derivative that is subtly wrong:

* Chunks are sampled **deterministically from a recorded seed**, so a failure
  reproduces exactly rather than being a story about a bad night.
* Each sampled chunk is recomputed **from the source**, by applying the same
  reduction to the corresponding source region — not compared against the level
  above, which would happily agree with a consistently wrong pyramid.
* Comparison is on SHA-256 of the bytes, so a single flipped voxel fails.

Readiness flips only if every sampled chunk matches. A derivative that fails is
left un-promoted with its failure recorded — never silently marked ready, and
never auto-deleted, because a bad derivative is evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .downsample import reduce_block
from .ladder import MagLevel, relative_factors
from .store import digest

#: How many chunks to sample per level. Enough that a systematic error is
#: caught with near-certainty, small enough that validation is not a second
#: build.
DEFAULT_SAMPLES_PER_LEVEL = 4


@dataclass
class ValidationResult:
    ok: bool
    checked: int = 0
    seed: int = 0
    failures: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "seed": self.seed,
            "failures": self.failures,
        }


def sample_origins(
    level: MagLevel,
    chunks: tuple[int, int, int],
    *,
    seed: int,
    samples: int,
) -> list[tuple[int, int, int]]:
    """Deterministic chunk origins to check for one level."""
    rng = np.random.default_rng(seed + level.level)
    grid = [
        max(1, -(-level.shape[axis] // chunks[axis])) for axis in range(3)
    ]
    total = grid[0] * grid[1] * grid[2]
    take = min(samples, total)
    picked = rng.choice(total, size=take, replace=False)

    origins = []
    for flat in sorted(int(p) for p in picked):
        gz, rem = divmod(flat, grid[1] * grid[2])
        gy, gx = divmod(rem, grid[2])
        origins.append((gz * chunks[0], gy * chunks[1], gx * chunks[2]))
    return origins


def expected_region(
    source: np.ndarray,
    level: MagLevel,
    origin: tuple[int, int, int],
    extent: tuple[int, int, int],
    *,
    reduction: str,
    chain: list[MagLevel] | None = None,
) -> np.ndarray:
    """Recompute one region of ``level`` from the **source**.

    Always starts at full resolution — never from the level above, because a
    pyramid whose levels are all wrong in the same way would happily pass a
    level-to-level comparison.

    It does, however, apply the *same successive reduction the build applies*:
    level n is built from level n-1, and for integer data that is not the same
    as one reduction by the absolute factor. Averaging 4 uint16 values and
    rounding, then averaging 4 of those and rounding, legitimately differs from
    averaging all 16 and rounding once. Modelling one step when the build takes
    several would make validation reject correct derivatives — and, worse, tempt
    someone to "fix" it by loosening the comparison to a tolerance, which is how
    a checksum stops being a checksum.

    ``chain`` is the ladder from full resolution up to ``level``. Without it the
    single-step form is used, which is correct for level 1 and for any ladder
    whose level is reached in one hop.
    """
    fz, fy, fx = level.factors
    z0, y0, x0 = origin
    dz, dy, dx = extent

    src = source[
        z0 * fz : (z0 + dz) * fz,
        y0 * fy : (y0 + dy) * fy,
        x0 * fx : (x0 + dx) * fx,
    ]
    if src.size == 0:
        return np.zeros((0, 0, 0), dtype=source.dtype)

    if not chain or len(chain) < 2:
        return reduce_block(src, level.factors, reduction=reduction)

    current = src
    for parent, child in zip(chain, chain[1:]):
        current = reduce_block(
            current, relative_factors(child, parent), reduction=reduction
        )
    return current


def validate_level(
    group,
    source: np.ndarray,
    level: MagLevel,
    *,
    reduction: str,
    seed: int,
    samples: int = DEFAULT_SAMPLES_PER_LEVEL,
    chain: list[MagLevel] | None = None,
) -> ValidationResult:
    """Check ``samples`` random chunks of one level against the source."""
    array = group[level.name]
    chunks = tuple(int(c) for c in array.chunks)
    result = ValidationResult(ok=True, seed=seed)

    for origin in sample_origins(level, chunks, seed=seed, samples=samples):
        extent = tuple(
            min(chunks[axis], level.shape[axis] - origin[axis]) for axis in range(3)
        )
        if any(e <= 0 for e in extent):
            continue

        stored = np.asarray(
            array[
                origin[0] : origin[0] + extent[0],
                origin[1] : origin[1] + extent[1],
                origin[2] : origin[2] + extent[2],
            ]
        )
        expected = expected_region(
            source, level, origin, extent, reduction=reduction, chain=chain
        )
        result.checked += 1

        if stored.shape != expected.shape or digest(stored) != digest(expected):
            result.ok = False
            result.failures.append(
                {
                    "level": level.level,
                    "mag": level.name,
                    "origin": list(origin),
                    "extent": list(extent),
                    "stored_digest": digest(stored),
                    "expected_digest": digest(expected),
                }
            )

    return result


def validate_pyramid(
    group,
    source: np.ndarray,
    levels: list[MagLevel],
    *,
    reduction: str,
    seed: int,
    samples: int = DEFAULT_SAMPLES_PER_LEVEL,
) -> ValidationResult:
    """Validate every level. One bad chunk fails the whole derivative."""
    overall = ValidationResult(ok=True, seed=seed)
    for level in levels:
        if level.level == 0:
            # Full resolution is a copy of the source; checking it still catches
            # a truncated or mis-shaped write.
            pass
        outcome = validate_level(
            group, source, level, reduction=reduction, seed=seed,
            samples=samples, chain=levels[: level.level + 1],
        )
        overall.checked += outcome.checked
        if not outcome.ok:
            overall.ok = False
            overall.failures.extend(outcome.failures)
    return overall
