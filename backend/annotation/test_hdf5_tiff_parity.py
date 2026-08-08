"""HDF5 and TIFF must be the same product, not two code paths that mostly agree.

``test_hdf5_source.py`` asserts equivalence at the IO layer. This module
asserts it at the layer users actually touch: the same voxels are registered
twice — once as ``.tif``, once as ``.h5`` — and every viewer/editor surface is
run against both, comparing **values**, not status codes.

Why value-by-value and not "does it 200": every failure worth catching here
returns something. The bug that prompted this module is the shape of them all
— the whole-volume label readers (Labels panel "All", the 3D mesh panel, the
legacy voxel-grid preview) opened the label file with ``tifffile.memmap``
directly. That is right for the *working copy*, which this app always writes as
TIFF, and wrong for the volume's *official* label, which is whatever the
manager registered. So the panels worked in Annotate (a TIFF working copy had
been seeded) and raised ``TiffFileError`` on a public share of the same volume
(no working copy, so the ``.h5`` source was read). Two surfaces, one volume,
different answers — invisible to any test that exercised only the editor.

The comparison is deliberately blunt: run it both ways, normalise the two
things that differ *by construction* (the fixture's filename stem and the
source file's extension), and require the rest to be identical. A new format
branch anywhere downstream fails here without anyone having to predict where.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from annotation.models import AnnotationTask
from annotation.visualization import slice_io
from core.choices import TaskType
from projects.models import Dataset, Project
from volumes.models import Volume

h5py = None
try:
    import h5py  # noqa: F811
except ImportError:  # pragma: no cover - only on an incomplete environment
    pass
nib = None
try:
    import nibabel as nib  # noqa: F811
except ImportError:  # pragma: no cover - only on an incomplete environment
    pass

User = get_user_model()

SHAPE = (6, 32, 32)
FULL_ON = override_settings(
    FEATURE_INTERPOLATION=True, FEATURE_ANNOTATION_OPS=True
)


class FormatParityTestCase(TestCase):
    """One project holding the same volume twice, in both formats."""

    def setUp(self):
        if h5py is None:  # pragma: no cover
            self.skipTest("h5py is not installed")
        if nib is None:  # pragma: no cover
            self.skipTest("nibabel is not installed")
        self.tmp = tempfile.TemporaryDirectory(prefix="mito-parity-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(slice_io.clear_caches)
        from core.utils import clear_header_cache

        clear_header_cache()
        self.addCleanup(clear_header_cache)

        base = Path(self.tmp.name)
        self.root = base / "data"
        self.root.mkdir()
        src = base / "src"
        src.mkdir()

        image = (np.arange(int(np.prod(SHAPE))) % 251).astype(np.uint8).reshape(SHAPE)
        labels = np.zeros(SHAPE, np.uint16)
        labels[1:5, 6:16, 6:16] = 3
        labels[2:4, 20:28, 20:28] = 9

        # Distinct stems on purpose. Two volumes whose image stems collide in
        # one dataset folder get the documented `_v<id>` working-mask fallback,
        # which reads as a format difference and is not one.
        tifffile.imwrite(str(src / "ct_im.tif"), image)
        tifffile.imwrite(str(src / "ct_mask.tif"), labels)
        self._write_h5(src / "ch_im.h5", image)
        self._write_h5(src / "ch_mask.h5", labels)
        self._write_nifti(src / "cn_im.nii.gz", image)
        self._write_nifti(src / "cn_mask.nii.gz", labels)

        self.user = User.objects.create_user(username="parity", password="x")
        project = Project.objects.create(title="Parity", created_by=self.user)
        dataset = Dataset.objects.create(project=project, name="ds")

        def volume(name, image_path, label_path):
            return Volume.objects.create(
                project=project, dataset=dataset, name=name,
                image_path=str(image_path), label_path=str(label_path),
                shape_x=SHAPE[2], shape_y=SHAPE[1], shape_z=SHAPE[0],
            )

        self.volumes = {
            "tif": volume("t", src / "ct_im.tif", src / "ct_mask.tif"),
            "h5": volume("h", src / "ch_im.h5", src / "ch_mask.h5"),
            "nii": volume("n", src / "cn_im.nii.gz", src / "cn_mask.nii.gz"),
        }
        self.tasks = {
            key: AnnotationTask.objects.create(
                project=project, volume=vol, assigned_to=self.user,
                z_start=0, z_end=SHAPE[0], y_end=SHAPE[1], x_end=SHAPE[2],
                task_type=TaskType.MANUAL_ANNOTATION,
            )
            for key, vol in self.volumes.items()
        }
        self.client.force_login(self.user)

    @staticmethod
    def _write_h5(path: Path, array: np.ndarray) -> Path:
        with h5py.File(str(path), "w") as handle:
            handle.create_dataset(
                "main", data=array, chunks=(2, 8, 8), compression="gzip"
            )
        return path

    @staticmethod
    def _write_nifti(path: Path, array: np.ndarray) -> Path:
        image = nib.Nifti1Image(array.transpose(2, 1, 0), np.eye(4))
        image.header.set_xyzt_units("micron")
        nib.save(image, str(path))
        return path

    # --- the comparison ---------------------------------------------------

    def _normalise(self, text: str) -> str:
        """Erase what differs by construction, so anything left is a real
        divergence: the fixture stems, the tempdir, and the source extension."""
        text = re.sub(r"c[thn]_", "", text)
        text = text.replace(str(self.tmp.name), "<tmp>")
        text = re.sub(r"\.(h5|tif|nii(?:\.gz)?)\b", ".<ext>", text)
        # The two volumes are two rows, so their ids (and any URL built from
        # one) differ for a reason that has nothing to do with the format.
        for attr in ("volume_id", "task_id", "id"):
            text = re.sub(rf"'{attr}': \d+", f"'{attr}': <id>", text)
        return re.sub(r"/(volumes|tasks)/\d+", r"/\1/<id>", text)

    def assertSameForBothFormats(self, describe, fn):
        """Run ``fn(volume, task)`` for both formats; require identical results.

        An exception is compared as a value rather than raised, so "both refuse
        the same way" counts as parity — but the caller is expected to pick
        inputs where both *succeed*, since two identical failures would
        otherwise pass while proving nothing.
        """
        results = {}
        for key in ("tif", "h5", "nii"):
            slice_io.clear_caches()
            try:
                results[key] = "ok:" + repr(fn(self.volumes[key], self.tasks[key]))
            except Exception as exc:  # noqa: BLE001 - compared, not swallowed
                results[key] = f"raised:{type(exc).__name__}: {exc}"
        tif = self._normalise(results["tif"])
        for key in ("h5", "nii"):
            candidate = self._normalise(results[key])
            self.assertEqual(
                tif, candidate,
                f"{describe} differs between .tif and {key}\n"
                f"  tif: {tif[:400]}\n  {key}: {candidate[:400]}",
            )
        self.assertTrue(
            tif.startswith("ok:"),
            f"{describe} failed for BOTH formats, so it proves nothing: {tif[:400]}",
        )
        return results["tif"]

    def _seed_working_copies(self):
        from annotation.services import get_label_slice_ids

        for volume in self.volumes.values():
            get_label_slice_ids(volume, "z", 0)


class ImageAndMetadataParity(FormatParityTestCase):
    def test_meta_display_range_and_rendered_slices_match(self):
        with override_settings(MITO_DATA_ROOT=self.root):
            self.assertSameForBothFormats(
                "volume_meta", lambda v, t: slice_io.volume_meta(v.image_location))
            self.assertSameForBothFormats(
                "display_range", lambda v, t: slice_io.display_range(v.image_location))
            for name, render in (
                ("image jpeg", slice_io.render_image_slice_jpeg),
                ("image png", slice_io.render_image_slice_png),
            ):
                self.assertSameForBothFormats(
                    name, lambda v, t, r=render: len(r(v.image_location, "z", 2)))
            self.assertSameForBothFormats(
                "label overlay png",
                lambda v, t: len(slice_io.render_label_slice_png(v.label_location, "z", 2)))
            self.assertSameForBothFormats(
                "region overlay png",
                lambda v, t: len(slice_io.render_region_mask_slice_png(v.label_location, "z", 2)))

    def test_every_axis_reads_the_same_voxels(self):
        """A format that only agrees on z would still break Y/X navigation."""
        with override_settings(MITO_DATA_ROOT=self.root):
            for axis in ("z", "y", "x"):
                self.assertSameForBothFormats(
                    f"read_slice axis={axis}",
                    lambda v, t, a=axis: np.asarray(
                        slice_io.read_slice(v.label_location, a, 2)
                    ).tolist())

    def test_registration_metadata_matches(self):
        from core.utils import inspect_volume_shape

        with override_settings(MITO_DATA_ROOT=self.root):
            self.assertSameForBothFormats(
                "inspect_volume_shape",
                lambda v, t: inspect_volume_shape(Path(v.image_location)))


class ReadOnlyLabelSurfaceParity(FormatParityTestCase):
    """View and public shares read the **official** label — the registered
    file itself. This is the case the editor's TIFF working copy hides."""

    def test_whole_volume_label_readers_match_without_a_working_copy(self):
        from annotation.services import (
            get_labels_3d_mesh,
            get_labels_3d_preview,
            get_labels_summary,
        )

        with override_settings(MITO_DATA_ROOT=self.root):
            self.assertSameForBothFormats(
                "labels_summary(readonly)",
                lambda v, t: get_labels_summary(v, readonly=True)["stats"])
            self.assertSameForBothFormats(
                "labels_3d_mesh(readonly)",
                lambda v, t: [m["id"] for m in
                              get_labels_3d_mesh(v, [3, 9], readonly=True)["meshes"]])
            self.assertSameForBothFormats(
                "labels_3d_preview(readonly)",
                lambda v, t: sorted(
                    get_labels_3d_preview(v, [3, 9], readonly=True)["grids"]))

    def test_readonly_slice_and_max_id_match(self):
        from annotation.services import (
            get_label_max_id_readonly,
            get_label_slice_ids_readonly,
        )

        with override_settings(MITO_DATA_ROOT=self.root):
            self.assertSameForBothFormats(
                "label_slice_ids_readonly",
                lambda v, t: get_label_slice_ids_readonly(v, "z", 2)["runs"])
            self.assertSameForBothFormats(
                "label_max_id_readonly", lambda v, t: get_label_max_id_readonly(v))

    def test_public_share_endpoints_match_over_http(self):
        """The surface the bug actually reached: a shared link has no working
        copy, so every panel on it reads the registered file directly."""
        from annotation.services import create_hard_case

        tokens = {
            key: create_hard_case(task=task, user=self.user, label_id=3).token
            for key, task in self.tasks.items()
        }

        def get(key, suffix, **params):
            return self.client.get(
                f"/api/public/hard-cases/{tokens[key]}/{suffix}", params
            )

        with override_settings(MITO_DATA_ROOT=self.root):
            for suffix, extract in (
                ("meta/", lambda r: {k: v for k, v in r.json().items()
                                     if k in ("shape", "dtype", "axes", "has_label")}),
                ("labels-summary/", lambda r: r.json()["stats"]),
                ("label-ids/?axis=z&index=2", lambda r: r.json()["runs"]),
                ("label-state/", lambda r: r.json()),
            ):
                responses = {k: get(k, suffix) for k in ("tif", "h5")}
                for key, resp in responses.items():
                    self.assertEqual(
                        resp.status_code, 200,
                        f"{suffix} failed for {key}: {resp.content[:300]}")
                self.assertEqual(
                    extract(responses["tif"]), extract(responses["h5"]),
                    f"public {suffix} differs between .tif and .h5")

            meshes = {
                k: self.client.post(
                    f"/api/public/hard-cases/{tokens[k]}/labels-3d-mesh/",
                    {"labels": [3, 9]}, content_type="application/json")
                for k in ("tif", "h5")
            }
            for key, resp in meshes.items():
                self.assertEqual(
                    resp.status_code, 200,
                    f"labels-3d-mesh failed for {key}: {resp.content[:300]}")
            # Byte-identical geometry: same voxels in, same surface out.
            self.assertEqual(meshes["tif"].content, meshes["h5"].content)


class EditorParity(FormatParityTestCase):
    def test_editor_surfaces_match_once_a_working_copy_exists(self):
        from annotation.services import (
            _ai_embedding_cache_path,
            get_label_max_id,
            get_label_slice_ids,
            get_labels_3d_mesh,
            get_labels_summary,
        )

        with override_settings(MITO_DATA_ROOT=self.root):
            self._seed_working_copies()
            self.assertSameForBothFormats(
                "labels_summary(editor)", lambda v, t: get_labels_summary(v)["stats"])
            self.assertSameForBothFormats(
                "labels_3d_mesh(editor)",
                lambda v, t: [m["id"] for m in get_labels_3d_mesh(v, [3, 9])["meshes"]])
            self.assertSameForBothFormats(
                "label_slice_ids", lambda v, t: get_label_slice_ids(v, "z", 2)["runs"])
            self.assertSameForBothFormats(
                "label_max_id", lambda v, t: get_label_max_id(v))
            self.assertSameForBothFormats(
                "ai embedding cache path",
                lambda v, t: Path(str(_ai_embedding_cache_path(v, "z", 2))).name)

    def test_the_h5_working_copy_carries_the_registered_ids(self):
        """Seeding is where an h5 label could silently become an empty mask."""
        from annotation.services import get_label_slice_ids

        with override_settings(MITO_DATA_ROOT=self.root):
            self.assertSameForBothFormats(
                "seeded ids",
                lambda v, t: sorted(
                    {i for i, _ in get_label_slice_ids(v, "z", 2)["runs"]}))

    def test_whole_volume_mutators_match(self):
        from annotation.services import (
            run_merge_labels_task,
            run_split_components_task,
            set_label_lifecycle_action,
            set_label_slice_ids,
        )

        with override_settings(MITO_DATA_ROOT=self.root):
            self._seed_working_copies()
            self.assertSameForBothFormats(
                "split_components",
                lambda v, t: run_split_components_task(t, 3, size_threshold=1))
            self.assertSameForBothFormats(
                "merge_labels", lambda v, t: run_merge_labels_task(t, 3, 9))
            self.assertSameForBothFormats(
                "set_label_slice_ids",
                lambda v, t: set_label_slice_ids(
                    v, "z", 3, [SHAPE[1], SHAPE[2]],
                    [[5, 20], [0, SHAPE[1] * SHAPE[2] - 20]]))
            self.assertSameForBothFormats(
                "lifecycle verify",
                lambda v, t: set_label_lifecycle_action(v, 5, "verify")["state"])

    def test_the_registered_source_is_never_written_by_either_format(self):
        import hashlib

        from annotation.services import set_label_slice_ids

        digests = {
            k: hashlib.sha256(Path(v.label_location).read_bytes()).hexdigest()
            for k, v in self.volumes.items()
        }
        with override_settings(MITO_DATA_ROOT=self.root):
            for volume in self.volumes.values():
                set_label_slice_ids(
                    volume, "z", 1, [SHAPE[1], SHAPE[2]],
                    [[8, 30], [0, SHAPE[1] * SHAPE[2] - 30]])
        for key, volume in self.volumes.items():
            self.assertEqual(
                hashlib.sha256(Path(volume.label_location).read_bytes()).hexdigest(),
                digests[key], f"the registered {key} label was modified")


@FULL_ON
class InterpolationParity(FormatParityTestCase):
    def test_plan_and_apply_match(self):
        from annotation.services import (
            apply_task_interpolation,
            plan_task_interpolation,
            set_label_slice_ids,
        )

        def paint_endpoints(volume):
            first = np.zeros((SHAPE[1], SHAPE[2]), np.int32)
            first[8:24, 8:24] = 4
            last = np.zeros((SHAPE[1], SHAPE[2]), np.int32)
            last[10:22, 10:22] = 4
            for index, plane in ((0, first), (4, last)):
                set_label_slice_ids(
                    volume, "z", index, [SHAPE[1], SHAPE[2]],
                    slice_io.encode_label_rle(plane))

        with override_settings(MITO_DATA_ROOT=self.root):
            for volume in self.volumes.values():
                paint_endpoints(volume)
            self.assertSameForBothFormats(
                "plan_task_interpolation",
                lambda v, t: plan_task_interpolation(
                    t, axis="z", first_index=0, last_index=4, label_id=4)["slices"])
            self.assertSameForBothFormats(
                "apply_task_interpolation",
                lambda v, t: apply_task_interpolation(
                    t, self.user, axis="z", first_index=0, last_index=4,
                    label_id=4)["slices_written"])


class LifecycleParity(FormatParityTestCase):
    def test_visualization_state_matches(self):
        from annotation.services import get_visualization_state

        with override_settings(MITO_DATA_ROOT=self.root):
            self.assertSameForBothFormats(
                "visualization state",
                lambda v, t: {k: val for k, val in get_visualization_state(v).items()
                              if k not in ("image_path", "label_path", "meta")})

    def test_approval_promotes_a_tiff_working_copy_for_both(self):
        """Approval repoints the official label at the working copy, which this
        app always writes as TIFF — an h5 volume must end up owning a TIFF
        label, not keep pointing at the registered source."""
        from annotation.services import _promote_working_label_to_official

        with override_settings(MITO_DATA_ROOT=self.root):
            self._seed_working_copies()
            for volume in self.volumes.values():
                _promote_working_label_to_official(volume)
            for key, volume in self.volumes.items():
                self.assertTrue(
                    volume.label_path.endswith(".tif"),
                    f"{key} promoted to {volume.label_path}")
            self.assertSameForBothFormats(
                "official label reads back",
                lambda v, t: np.asarray(
                    slice_io.read_slice(v.label_location, "z", 2)).tolist())
