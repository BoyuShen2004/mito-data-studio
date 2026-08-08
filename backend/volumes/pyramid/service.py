"""Building a volume's pyramid — the only boundary callers use.

Doc 20 §Pyramid job: *"ProcessingJob(type=build_pyramid) → Slurm/local → write
derivative → validate random chunk checksums → mark Volume ready_streaming=true."*
That order is the contract, and it is why readiness is the last thing that
happens rather than the first.

Three properties the build is built around:

* **Bounded.** Each level is produced from the level above in z-slabs, so peak
  memory is a plane times the z factor, never the volume. A multi-gigabyte
  volume cannot be loaded to downsample itself.
* **Additive.** The source TIFF is opened read-only and never modified. Rolling
  back is deleting a directory.
* **Promote-after-validate.** The build writes to a sibling ``.building``
  directory and is moved into place only once every sampled chunk matches, so a
  crash never leaves a half-pyramid that looks finished.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from django.conf import settings
from django.db import transaction

from . import store
from .downsample import REDUCTION_MEAN, REDUCTION_MODE, default_reduction, reduce_slab, slab_plan
from .ladder import build_ladder, chunk_shape_for, relative_factors
from .validate import DEFAULT_SAMPLES_PER_LEVEL, validate_pyramid

__all__ = [
    "pyramids_enabled",
    "build_pyramid",
    "PyramidBuildError",
    "PyramidConflict",
    "BuildReport",
]


class PyramidBuildError(RuntimeError):
    """The pyramid could not be built."""

    def __init__(self, message: str, *, reason: str = "invalid"):
        super().__init__(message)
        self.reason = reason


class PyramidConflict(PyramidBuildError):
    """Another build is already in progress for this volume."""

    def __init__(self, message: str):
        super().__init__(message, reason="conflict")


@dataclass
class BuildReport:
    """Everything the job record and the benchmark want to know."""

    volume_id: int
    layer: str = store.LAYER_IMAGE
    levels: list[dict] = field(default_factory=list)
    checked_chunks: int = 0
    seed: int = 0
    reduction: str = REDUCTION_MEAN
    peak_slab_voxels: int = 0
    bytes_written: int = 0
    path: str = ""
    validated: bool = False

    def as_dict(self) -> dict:
        return {
            "volume_id": self.volume_id,
            "layer": self.layer,
            "levels": self.levels,
            "checked_chunks": self.checked_chunks,
            "seed": self.seed,
            "reduction": self.reduction,
            "peak_slab_voxels": self.peak_slab_voxels,
            "bytes_written": self.bytes_written,
            "path": self.path,
            "validated": self.validated,
        }


def pyramids_enabled() -> bool:
    return bool(getattr(settings, "FEATURE_VOLUME_PYRAMIDS", False))


def _voxel_size(volume) -> tuple[float, float, float]:
    """``(z, y, x)`` physical voxel size, defaulting to isotropic."""
    values = (
        getattr(volume, "voxel_size_z", None),
        getattr(volume, "voxel_size_y", None),
        getattr(volume, "voxel_size_x", None),
    )
    if any(v is None or float(v) <= 0 for v in values):
        return (1.0, 1.0, 1.0)
    return (float(values[0]), float(values[1]), float(values[2]))


def _open_source(volume, layer: str = store.LAYER_IMAGE):
    """The layer's source array-like object, lazy and **read-only**.

    Read-only is not decoration: a registered source image may live in someone
    else's tree, and a conversion that mutated its input would be exactly the
    failure the data-root work eliminated. The same holds for a region mask,
    which is an immutable reference layer by product definition.
    """
    from annotation.visualization.slice_io import _open_volume, resolve_path

    location = store.layer_source(volume, layer)
    if not location:
        if store.require_layer(layer) == store.LAYER_REGION:
            raise PyramidBuildError(
                "Volume has no region mask.", reason="no_region_mask"
            )
        raise PyramidBuildError("Volume has no image.", reason="no_image")
    # ``_open_volume`` is the shared TIFF/HDF5/NIfTI boundary. Do not wrap the
    # lazy HDF5/NIfTI adapters in ``np.asarray`` here: that materializes an
    # entire multi-GB source before the bounded slab loop gets a chance to run.
    view = _open_volume(resolve_path(location))
    if view.ndim != 3:
        raise PyramidBuildError(
            f"Expected a 3-D volume, got shape {view.shape}.", reason="not_3d"
        )
    return view


def build_pyramid(
    volume,
    *,
    layer: str = store.LAYER_IMAGE,
    is_label: bool | None = None,
    samples_per_level: int = DEFAULT_SAMPLES_PER_LEVEL,
    seed: int = 20260730,
    plane: int = 512,
) -> BuildReport:
    """Build, validate and promote one layer of a volume's derivative.

    ``layer`` is ``"image"`` (mean reduction) or ``"region"`` (the immutable
    ROI, which is categorical and therefore reduced by **mode** — averaging a
    binary mask would smear its boundary into values that are neither inside
    nor outside). ``is_label`` defaults to that per-layer choice and stays
    overridable for a label-valued image source.

    Returns a :class:`BuildReport`. Raises :class:`PyramidBuildError` (or
    :class:`PyramidConflict`) without promoting anything on failure.
    """
    if not pyramids_enabled():
        raise PyramidBuildError(
            "Volume pyramids are not enabled on this instance.", reason="disabled"
        )

    try:
        layer = store.require_layer(layer)
    except store.PyramidStoreError as exc:
        raise PyramidBuildError(str(exc), reason="unknown_layer") from exc

    zarr = store.require_zarr()  # fail early and clearly if the dep is absent
    del zarr

    source = _open_source(volume, layer)
    dtype = source.dtype
    voxel = _voxel_size(volume)
    if is_label is None:
        is_label = layer == store.LAYER_REGION
    reduction = default_reduction(dtype, is_label=is_label)
    levels = build_ladder(tuple(int(s) for s in source.shape), voxel)

    location = store.pyramid_location(volume, layer)
    if location.tmp_path.exists():
        raise PyramidConflict(
            f"A {layer} build is already in progress for volume {volume.pk}."
        )

    report = BuildReport(
        volume_id=volume.pk,
        layer=layer,
        seed=seed,
        reduction=reduction,
        path=location.rel_path,
    )

    attributes = store.build_attributes(
        source_rel=str(store.layer_source(volume, layer)),
        dtype=dtype,
        voxel_size=voxel,
        ladder=levels,
        reduction=reduction,
        checksum_seed=seed,
        layer=layer,
    )

    try:
        group = store.create_group(location.tmp_path, attributes=attributes)

        previous = None
        for level in levels:
            array = store.create_level_array(
                group, level, dtype, voxel_size=voxel, plane=plane
            )
            if level.level == 0:
                # Full resolution: copy plane by plane, so even mag 1 never
                # holds the whole volume in memory.
                for z in range(level.shape[0]):
                    array[z] = source[z]
                report.peak_slab_voxels = max(
                    report.peak_slab_voxels, int(np.prod(source.shape[1:]))
                )
            else:
                parent = group[previous.name]
                factors = relative_factors(level, previous)
                plan = slab_plan(previous.shape[0], factors[0])
                for out_z, (z0, z1) in enumerate(plan):
                    if out_z >= level.shape[0]:
                        break
                    slab = np.asarray(parent[z0:z1])
                    report.peak_slab_voxels = max(report.peak_slab_voxels, slab.size)
                    array[out_z] = reduce_slab(slab, factors, reduction=reduction)[0]

            report.levels.append(
                {
                    "level": level.level,
                    "mag": level.name,
                    "shape": list(level.shape),
                    "factors": list(level.factors),
                    "chunks": list(chunk_shape_for(level.shape, plane=plane)),
                }
            )
            previous = level

        outcome = validate_pyramid(
            group,
            source,
            levels,
            reduction=reduction,
            seed=seed,
            samples=samples_per_level,
        )
        report.checked_chunks = outcome.checked
        report.validated = outcome.ok
        if not outcome.ok:
            raise PyramidBuildError(
                f"Checksum validation failed for {len(outcome.failures)} chunk(s); "
                "the derivative was not promoted.",
                reason="validation_failed",
            )

        store.promote(location)
    except Exception:
        # Never leave a half-pyramid that looks finished. The source is
        # untouched either way.
        store.discard_build(location)
        raise

    usage = store.store_usage(volume, layer=layer)
    report.bytes_written = int(usage.get("bytes", 0))

    _mark_ready(volume, report)
    return report


def _mark_ready(volume, report: BuildReport) -> None:
    """Flip readiness **after** validation, under a row lock.

    The lock is what stops two concurrent builds from both declaring victory
    with different derivatives; the loser sees the winner's metadata. Only the
    built layer's two fields are written, so an image build never disturbs a
    region derivative promoted a second earlier (or the reverse).
    """
    from volumes.models import Volume

    ready_field, metadata_field = store.LAYER_FIELDS[report.layer]
    with transaction.atomic():
        locked = Volume.objects.select_for_update().get(pk=volume.pk)
        setattr(locked, ready_field, True)
        setattr(
            locked,
            metadata_field,
            {
                **store.describe(locked, layer=report.layer),
                "checked_chunks": report.checked_chunks,
                "checksum_seed": report.seed,
                "reduction": report.reduction,
            },
        )
        locked.save(update_fields=[ready_field, metadata_field])
    volume.refresh_from_db(fields=[ready_field, metadata_field])


def clear_pyramid(volume, *, layer: str = store.LAYER_IMAGE) -> bool:
    """Remove a derivative and un-mark readiness. Rollback for §10 of ADR-009.

    The source is untouched, which is the entire point of "conversion is
    additive": rolling back costs a directory, never data.
    """
    from volumes.models import Volume

    layer = store.require_layer(layer)
    location = store.pyramid_location(volume, layer)
    existed = location.path.exists()
    store._remove_tree(location.path)
    store.discard_build(location)

    ready_field, metadata_field = store.LAYER_FIELDS[layer]
    with transaction.atomic():
        locked = Volume.objects.select_for_update().get(pk=volume.pk)
        setattr(locked, ready_field, False)
        setattr(locked, metadata_field, {})
        locked.save(update_fields=[ready_field, metadata_field])
    volume.refresh_from_db(fields=[ready_field, metadata_field])
    return existed
