"""API-level tests for WK-style interpolation (ADR-006), the half that
``test_interpolation.py`` deliberately does not cover.

``test_interpolation.py`` proves the *mathematics* (golden fixtures) and the
*service contract* (one operation, idempotency, version conflicts) against
injected arrays and an injected ``write_slice``. Neither of those touches a
volume on disk, which is exactly where the endpoint added for the Annotate
toolbar can go wrong: planning against the official label instead of the
working copy, writing the endpoints as well as the intermediates, honouring
the wrong overwrite policy, or previewing and applying different geometry.

Every failure mode above returns 200, so the assertions here are about what
landed in the working volume, not about status codes.

Runs against an isolated temporary ``MITO_DATA_ROOT`` with a source image in
its own tempdir — nothing here can reach real microscopy data.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from annotation.models import AnnotationOperation, AnnotationTask
from core.choices import TaskType
from projects.models import Dataset, Project
from volumes.models import Volume

User = get_user_model()

# Six z-planes: paint the active label on z=0 and z=4, interpolate 1..3, and
# leave z=5 as a witness that nothing outside the requested span is touched.
SHAPE = (6, 24, 24)

FULL_ON = override_settings(
    FEATURE_INTERPOLATION=True, FEATURE_ANNOTATION_OPS=True
)


def _disc(cy, cx, radius, shape=(SHAPE[1], SHAPE[2])) -> np.ndarray:
    yy, xx = np.ogrid[: shape[0], : shape[1]]
    return (yy - cy) ** 2 + (xx - cx) ** 2 <= radius * radius


class InterpolationApiTestCase(TestCase):
    """One task whose working copy holds label 7 on z=0 and z=4 only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.root.mkdir(parents=True)

        external = Path(self.tmp.name) / "source"
        external.mkdir(parents=True)
        self.image = external / "cortex.tif"
        tifffile.imwrite(str(self.image), np.full(SHAPE, 180, dtype=np.uint8))

        self.user = User.objects.create_user(username="editor", password="x")
        self.project = Project.objects.create(title="Proj", created_by=self.user)
        self.dataset = Dataset.objects.create(project=self.project, name="DS")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="cortex",
            image_path=str(self.image),
        )
        self.task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume, assigned_to=self.user,
            z_start=0, z_end=SHAPE[0], y_end=SHAPE[1], x_end=SHAPE[2],
            task_type=TaskType.MANUAL_ANNOTATION,
        )
        self.client.force_login(self.user)

    # --- helpers ---------------------------------------------------------

    def _working_path(self) -> Path:
        from annotation.label_paths import working_label_rel_path
        from annotation.visualization.slice_io import resolve_path

        return resolve_path(working_label_rel_path(self.volume))

    def _seed(self, mask: np.ndarray) -> None:
        from annotation.services import _save_label_volume
        from annotation.visualization import slice_io

        slice_io.clear_caches()
        _save_label_volume(self.volume, mask)

    def _read_working(self) -> np.ndarray:
        from annotation.visualization.slice_io import read_label_array

        return np.asarray(read_label_array(self._working_path()))

    @staticmethod
    def _endpoints(label: int = 7) -> np.ndarray:
        """Label ``label`` as a disc on z=0 and a shifted disc on z=4."""
        mask = np.zeros(SHAPE, dtype=np.int32)
        mask[0][_disc(8, 8, 5)] = label
        mask[4][_disc(14, 14, 5)] = label
        return mask

    def _post(self, **body):
        payload = {
            "axis": "z", "first_index": 0, "last_index": 4, "label": 7,
            "mode": "preview",
        }
        payload.update(body)
        return self.client.post(
            f"/api/tasks/{self.task.pk}/interpolate/", payload,
            content_type="application/json",
        )


@FULL_ON
class PreviewTests(InterpolationApiTestCase):
    """Preview must be a *pure read*: renderable, and provably non-mutating."""

    def test_preview_returns_one_mask_per_intermediate_slice(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(self._endpoints())
            resp = self._post()
        self.assertEqual(resp.status_code, 200, resp.content[:400])
        data = resp.json()
        self.assertEqual([s["index"] for s in data["slices"]], [1, 2, 3])
        self.assertEqual(data["depth"], 4)
        self.assertEqual(data["label"], 7)
        self.assertGreater(data["voxels_changed"], 0)
        for entry in data["slices"]:
            self.assertEqual(entry["shape"], [SHAPE[1], SHAPE[2]])
            self.assertEqual(sum(count for _v, count in entry["runs"]),
                             SHAPE[1] * SHAPE[2])
            # 0/1 masks — the wire shape predict-mask already uses.
            self.assertLessEqual({v for v, _c in entry["runs"]}, {0, 1})

    def test_preview_writes_nothing(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(self._endpoints())
            before = self._read_working()
            self._post()
            after = self._read_working()
        np.testing.assert_array_equal(after, before)
        self.assertEqual(AnnotationOperation.objects.count(), 0)

    def test_preview_matches_what_apply_writes(self):
        """A confirm must land exactly the geometry the user was shown."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(self._endpoints())
            previewed = self._post().json()["slices"]
            self._post(mode="apply")
            written = self._read_working()
        for entry in previewed:
            expected = np.zeros(SHAPE[1] * SHAPE[2], dtype=np.int32)
            pos = 0
            for value, count in entry["runs"]:
                expected[pos:pos + count] = value
                pos += count
            np.testing.assert_array_equal(
                written[entry["index"]] == 7,
                expected.reshape(SHAPE[1], SHAPE[2]).astype(bool),
            )

    def test_an_endpoint_without_the_label_is_refused(self):
        """Interpolating from an empty endpoint is extrapolation."""
        mask = np.zeros(SHAPE, dtype=np.int32)
        mask[0][_disc(8, 8, 5)] = 7
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(mask)
            resp = self._post()
        self.assertEqual(resp.status_code, 400, resp.content[:300])
        self.assertEqual(resp.json()["reason"], "missing_endpoint_label")

    def test_out_of_range_slices_are_rejected(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(self._endpoints())
            resp = self._post(last_index=SHAPE[0] + 20)
        self.assertEqual(resp.status_code, 400, resp.content[:300])


@FULL_ON
class ApplyTests(InterpolationApiTestCase):
    def test_apply_writes_only_the_intermediates(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(self._endpoints())
            before = self._read_working()
            resp = self._post(mode="apply")
            after = self._read_working()
        self.assertEqual(resp.status_code, 200, resp.content[:400])
        self.assertEqual(resp.json()["slices_written"], [1, 2, 3])
        # Endpoints and the witness slice past the span are untouched.
        for z in (0, 4, 5):
            np.testing.assert_array_equal(after[z], before[z])
        for z in (1, 2, 3):
            self.assertTrue((after[z] == 7).any(), f"z={z} was not filled")

    def test_apply_records_exactly_one_undoable_operation(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(self._endpoints())
            resp = self._post(mode="apply")
        self.assertEqual(AnnotationOperation.objects.count(), 1)
        op = AnnotationOperation.objects.get()
        self.assertEqual(str(op.id), resp.json()["operation_id"])
        self.assertEqual(op.payload["algorithm"], "sdf-linear-blend")
        self.assertEqual(op.payload["label_id"], 7)
        # Metadata only — never voxels (Phase 7's bounded-payload rule).
        self.assertNotIn("masks", op.payload)

    def test_default_overwrite_policy_preserves_other_labels(self):
        """`empty` is the default: existing work is never silently destroyed."""
        mask = self._endpoints()
        mask[2][_disc(11, 11, 3)] = 9
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(mask)
            self._post(mode="apply")
            after = self._read_working()
        self.assertEqual(int((after[2] == 9).sum()), int(_disc(11, 11, 3).sum()))

    def test_overwrite_all_replaces_the_other_label(self):
        mask = self._endpoints()
        mask[2][_disc(11, 11, 3)] = 9
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(mask)
            resp = self._post(mode="apply", overwrite_mode="overwrite_all")
            after = self._read_working()
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.assertLess(int((after[2] == 9).sum()), int(_disc(11, 11, 3).sum()))

    def test_an_unknown_overwrite_mode_is_rejected(self):
        """No silent fallback: a misspelled policy must not quietly become
        the destructive one (or the conservative one)."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(self._endpoints())
            before = self._read_working()
            resp = self._post(mode="apply", overwrite_mode="all")
            after = self._read_working()
        self.assertEqual(resp.status_code, 400, resp.content[:300])
        self.assertEqual(resp.json()["reason"], "bad_overwrite_mode")
        np.testing.assert_array_equal(after, before)

    def test_replayed_idempotency_key_does_not_apply_twice(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(self._endpoints())
            first = self._post(mode="apply", idempotency_key="k-1")
            second = self._post(mode="apply", idempotency_key="k-1")
        self.assertEqual(second.status_code, 200, second.content[:300])
        self.assertEqual(first.json()["operation_id"],
                         second.json()["operation_id"])
        self.assertEqual(AnnotationOperation.objects.count(), 1)

    def test_swapped_endpoints_are_normalised(self):
        """"From 4 to 0" is the same request as "from 0 to 4"."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(self._endpoints())
            resp = self._post(mode="apply", first_index=4, last_index=0)
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.assertEqual(resp.json()["slices_written"], [1, 2, 3])

    def test_a_locked_task_is_refused(self):
        """An approved-and-closed task is view-only. `can_annotate_task`
        catches this before the service does, so the answer is 403 (no edit
        access) rather than the service's own 409 — what matters is that
        neither preview nor apply reaches the working copy."""
        self.task.annotation_locked = True
        self.task.save(update_fields=["annotation_locked"])
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(self._endpoints())
            before = self._read_working()
            resp = self._post(mode="apply")
            after = self._read_working()
        self.assertEqual(resp.status_code, 403, resp.content[:300])
        np.testing.assert_array_equal(after, before)


@FULL_ON
class PermissionTests(InterpolationApiTestCase):
    def test_a_user_without_edit_access_is_refused(self):
        stranger = User.objects.create_user(username="stranger", password="x")
        self.client.force_login(stranger)
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(self._endpoints())
            resp = self._post()
        self.assertEqual(resp.status_code, 403, resp.content[:300])


class FeatureFlagTests(InterpolationApiTestCase):
    """The route must exist even when the flag is off, so "not enabled" and
    "not deployed" stay distinguishable."""

    @override_settings(FEATURE_INTERPOLATION=False)
    def test_disabled_reports_503_not_404(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            resp = self._post()
        self.assertEqual(resp.status_code, 503, resp.content[:300])

    @override_settings(FEATURE_INTERPOLATION=True, FEATURE_ANNOTATION_OPS=False)
    def test_apply_needs_the_operations_flag(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(self._endpoints())
            before = self._read_working()
            resp = self._post(mode="apply")
            after = self._read_working()
        self.assertEqual(resp.status_code, 503, resp.content[:300])
        self.assertEqual(resp.json()["reason"], "operations_disabled")
        np.testing.assert_array_equal(after, before)

    @override_settings(FEATURE_INTERPOLATION=True, FEATURE_ANNOTATION_OPS=False)
    def test_preview_still_works_without_the_operations_flag(self):
        """Preview records nothing, so it does not need the ops flag."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve()):
            self._seed(self._endpoints())
            resp = self._post()
        self.assertEqual(resp.status_code, 200, resp.content[:300])
