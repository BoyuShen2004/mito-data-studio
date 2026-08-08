"""Phase 8 — interpolation golden and correctness tests.

The phase gate is **golden tests**, so this module's centre of gravity is
``GoldenFixtureTests``: fixtures whose expected outputs come from closed-form
geometry in ``golden/make_golden_fixtures.py``, which never imports the code
under test.

Tolerance is explicit and recorded in the manifest. For the exact cases the
analytic circle must be reproduced **exactly outside a one-voxel band** around
its boundary. The band exists because the specified algorithm uses a *discrete*
Euclidean distance transform — distance to the nearest opposite-class voxel
centre — while the closed form describes the *continuous* signed distance. They
agree in the interior and differ only at the rasterised surface; the measured
deviation across every exact case is at most 0.44 voxels.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

from accounts.models import AnnotatorProfile, UserProfile
from annotation.interpolation import core, service
from annotation.interpolation.core import InterpolationError
from annotation.models import AnnotationOperation, AnnotationTask
from annotation.operations import VersionConflict, current_version
from core.choices import TaskStatus, UserRole
from projects.models import Dataset, Project
from volumes.models import Volume

GOLDEN = Path(__file__).resolve().parent / "interpolation" / "golden"
MANIFEST = json.loads((GOLDEN / "manifest.json").read_text())
BAND = float(MANIFEST["tolerance_band_voxels"])

# Interpolation on, the Phase 7 operations log deliberately off — see the
# same note in test_tools.py: FEATURE_ANNOTATION_OPS defaults *on* under
# `production_integrated_v1`, so this has to say so rather than assume it.
INTERP_ON = override_settings(FEATURE_INTERPOLATION=True,
                              FEATURE_ANNOTATION_OPS=False)
INTERP_OFF = override_settings(FEATURE_INTERPOLATION=False)
FULL_ON = override_settings(FEATURE_INTERPOLATION=True,
                            FEATURE_ANNOTATION_OPS=True)

requires_postgres = unittest.skipUnless(
    connection.vendor == "postgresql", "row locking is PostgreSQL-only"
)



def _shifted_discs(*, separation: int, radius: int, shape=(64, 64)):
    """Two equal discs displaced along x, built by geometry.

    Used where the test is about a *property* (does spacing reach the metric?)
    rather than a golden value, so constructing inputs inline is legitimate —
    no expected output is derived from the implementation.
    """
    yy, xx = np.ogrid[:shape[0], :shape[1]]
    cy = shape[0] // 2
    out = []
    for cx in (shape[1] // 2 - separation // 2, shape[1] // 2 + separation // 2):
        m = ((yy - cy) ** 2 + (xx - cx) ** 2) <= radius ** 2
        arr = np.zeros(shape, np.uint8)
        arr[m] = 1
        out.append(arr)
    return out[0], out[1]


def load(case_id):
    case = next(c for c in MANIFEST["cases"] if c["id"] == case_id)
    return case, np.load(GOLDEN / case["fixture"])


def labels_from(mask, label_id=1, dtype=np.uint8):
    out = np.zeros(mask.shape, dtype=dtype)
    out[mask.astype(bool)] = label_id
    return out


# ---------------------------------------------------------------------------
# Golden fixtures — the phase gate
# ---------------------------------------------------------------------------


class GoldenFixtureTests(TestCase):
    """Pure mathematics; no database rows are created by any test here."""

    def test_manifest_lists_every_fixture(self):
        on_disk = {p.name for p in GOLDEN.glob("*.npz")}
        listed = {c["fixture"] for c in MANIFEST["cases"]}
        self.assertEqual(on_disk, listed)

    def test_fixtures_match_their_recorded_hashes(self):
        """An accidental regeneration must be visible, not silent."""
        import hashlib

        for case in MANIFEST["cases"]:
            z = np.load(GOLDEN / case["fixture"])
            for key, expected_hash in case["hashes"].items():
                actual = hashlib.sha256(
                    np.ascontiguousarray(z[key]).tobytes()
                ).hexdigest()
                self.assertEqual(
                    actual, expected_hash,
                    f"{case['id']}:{key} changed since the manifest was written",
                )

    def test_generator_does_not_import_the_core(self):
        """The golden expectations must stay independent of the implementation."""
        source = (GOLDEN / "make_golden_fixtures.py").read_text()
        for forbidden in ("from annotation.interpolation", "import core",
                          "interpolation.core"):
            self.assertNotIn(forbidden, source)

    def test_exact_cases_match_the_analytic_circle(self):
        """The load-bearing golden assertion.

        The blend of two concentric circles' signed distances is
        ``|p-c| - ((1-k)r1 + k*r2)``, so the interpolated shape is the circle
        of interpolated radius — derived from geometry, never from this code.
        """
        centre = (32, 32)
        checked = 0
        for case in MANIFEST["cases"]:
            if not case["expects_exact_output"]:
                continue
            z = np.load(GOLDEN / case["fixture"])
            first, last = labels_from(z["first"]), labels_from(z["last"])
            plan = core.plan(first, last, label_id=1, depth=case["depth"],
                             spacing=tuple(case["spacing"]))

            r1 = np.sqrt(int(z["first"].sum()) / np.pi)
            r2 = np.sqrt(int(z["last"].sum()) / np.pi)
            h, w = z["first"].shape
            yy, xx = np.ogrid[:h, :w]
            dist = np.sqrt((yy - centre[0]) ** 2 + (xx - centre[1]) ** 2)

            for offset in case["expected_offsets"]:
                k = int(offset) / case["depth"]
                r_k = (1 - k) * r1 + k * r2
                expected = z[f"expected_{offset}"]
                got = plan.masks[int(offset)]
                # Outside the rasterisation band, agreement must be exact.
                outside_band = np.abs(dist - r_k) > BAND
                np.testing.assert_array_equal(
                    got[outside_band], expected[outside_band],
                    err_msg=f"{case['id']} offset {offset} differs beyond the "
                            f"{BAND}-voxel rasterisation band",
                )
                checked += 1
        self.assertGreater(checked, 0, "no exact golden case ran")

    def test_every_fixture_produces_a_plan(self):
        """Including topology change, holes, thin and single-voxel objects."""
        for case in MANIFEST["cases"]:
            z = np.load(GOLDEN / case["fixture"])
            plan = core.plan(
                labels_from(z["first"]), labels_from(z["last"]),
                label_id=1, depth=case["depth"],
                spacing=tuple(case["spacing"]),
            )
            self.assertEqual(len(plan.masks), case["depth"] - 1, case["id"])

    def test_endpoints_are_reproduced(self):
        """k=0 and k=1 must return the endpoint masks exactly."""
        for case in MANIFEST["cases"]:
            z = np.load(GOLDEN / case["fixture"])
            first_mask = z["first"].astype(bool)
            last_mask = z["last"].astype(bool)
            spacing = tuple(case["spacing"])
            f_sdf = core.signed_distance(first_mask, spacing=spacing)
            l_sdf = core.signed_distance(last_mask, spacing=spacing)
            np.testing.assert_array_equal(
                core.blend(f_sdf, l_sdf, 0.0), first_mask, case["id"])
            np.testing.assert_array_equal(
                core.blend(f_sdf, l_sdf, 1.0), last_mask, case["id"])

    def test_identical_endpoints_give_identical_intermediates(self):
        case, z = load("cylinder_constant")
        plan = core.plan(labels_from(z["first"]), labels_from(z["last"]),
                         label_id=1, depth=case["depth"])
        first_mask = z["first"].astype(bool)
        for mask in plan.masks.values():
            np.testing.assert_array_equal(mask, first_mask)

    def test_topology_split_produces_intermediates(self):
        case, z = load("topology_split")
        plan = core.plan(labels_from(z["first"]), labels_from(z["last"]),
                         label_id=1, depth=case["depth"])
        for mask in plan.masks.values():
            self.assertTrue(mask.any(), "a topology change must not vanish")

    def test_hole_cases_preserve_the_hole_at_the_endpoint(self):
        case, z = load("hole_appearing")
        last_mask = z["last"].astype(bool)
        f = core.signed_distance(z["first"].astype(bool))
        l = core.signed_distance(last_mask)
        np.testing.assert_array_equal(core.blend(f, l, 1.0), last_mask)

    def test_thin_structure_does_not_disappear(self):
        case, z = load("thin_structure")
        plan = core.plan(labels_from(z["first"]), labels_from(z["last"]),
                         label_id=1, depth=case["depth"])
        for offset, mask in plan.masks.items():
            self.assertTrue(mask.any(), f"thin structure vanished at {offset}")

    def test_widely_separated_small_objects_yield_empty_intermediates(self):
        """A real limitation of SDF interpolation, pinned rather than hidden.

        Two isolated single voxels four apart have signed distance −1 only at
        themselves. At the midpoint both fields are positive, so the blend is
        positive everywhere and nothing is labelled. The same happens whenever
        the endpoints' negative regions do not overlap — here, discs of radius
        12 separated by 28.

        This is inherent to the specified algorithm, not a defect in this
        implementation, and doc 07 records upstream's own warning that "the
        heuristics may err". Asserted so the behaviour is documented and a
        future change to it is deliberate.
        """
        case, z = load("single_pixel")
        plan = core.plan(labels_from(z["first"]), labels_from(z["last"]),
                         label_id=1, depth=case["depth"])
        for offset, mask in plan.masks.items():
            self.assertFalse(
                mask.any(),
                f"offset {offset}: expected an empty intermediate for two "
                f"isolated voxels whose negative regions never overlap",
            )

    def test_translation_beyond_overlap_is_also_empty(self):
        """The same limitation at object scale: radius 8, centres 32 apart."""
        case, z = load("translation")
        plan = core.plan(labels_from(z["first"]), labels_from(z["last"]),
                         label_id=1, depth=case["depth"])
        self.assertFalse(plan.masks[2].any())

    def test_translation_within_overlap_does_interpolate(self):
        """The complement: once the negative regions meet, it works."""
        first, last = _shifted_discs(separation=10, radius=12)
        plan = core.plan(first, last, label_id=1, depth=4)
        for offset, mask in plan.masks.items():
            self.assertTrue(mask.any(), f"offset {offset} vanished")

    def test_boundary_touching_object(self):
        case, z = load("boundary_touching")
        plan = core.plan(labels_from(z["first"]), labels_from(z["last"]),
                         label_id=1, depth=case["depth"])
        self.assertTrue(all(m.any() for m in plan.masks.values()))


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class DeterminismTests(TestCase):
    def test_repeated_runs_are_byte_identical(self):
        case, z = load("topology_split")
        first, last = labels_from(z["first"]), labels_from(z["last"])
        a = core.plan(first, last, label_id=1, depth=8)
        b = core.plan(first, last, label_id=1, depth=8)
        for offset in a.masks:
            np.testing.assert_array_equal(a.masks[offset], b.masks[offset])

    def test_input_order_does_not_affect_output(self):
        """Reversing endpoints must mirror, not perturb."""
        case, z = load("cylinder_growing")
        first, last = labels_from(z["first"]), labels_from(z["last"])
        fwd = core.plan(first, last, label_id=1, depth=4)
        rev = core.plan(last, first, label_id=1, depth=4)
        np.testing.assert_array_equal(fwd.masks[1], rev.masks[3])
        np.testing.assert_array_equal(fwd.masks[3], rev.masks[1])

    def test_deterministic_in_a_fresh_process(self):
        """Rules out hash seeding and any process-local state."""
        case, z = load("hole_appearing")
        expected = core.plan(labels_from(z["first"]), labels_from(z["last"]),
                             label_id=1, depth=4).masks[2]
        script = (
            "import json,sys,numpy as np;"
            f"sys.path.insert(0,{str(Path(__file__).resolve().parents[1])!r});"
            "from annotation.interpolation import core;"
            f"z=np.load({str(GOLDEN / case['fixture'])!r});"
            "f=np.zeros(z['first'].shape,np.uint8);f[z['first'].astype(bool)]=1;"
            "l=np.zeros(z['last'].shape,np.uint8);l[z['last'].astype(bool)]=1;"
            "m=core.plan(f,l,label_id=1,depth=4).masks[2];"
            "print(int(m.sum()))"
        )
        out = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True,
            env={"PYTHONHASHSEED": "1", "PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        self.assertEqual(int(out.stdout.strip()), int(expected.sum()))


# ---------------------------------------------------------------------------
# Spacing / anisotropy
# ---------------------------------------------------------------------------


class SpacingTests(TestCase):
    def test_anisotropy_changes_the_result(self):
        """Spacing must actually reach the metric, not be ignored.

        Uses overlapping discs deliberately: the `translation` fixture has
        centres 32 apart, which is past the overlap limit, so every
        intermediate there is empty and the comparison would trivially hold as
        0 == 0 while proving nothing.
        """
        first, last = _shifted_discs(separation=10, radius=12)
        iso = core.plan(first, last, label_id=1, depth=4, spacing=(1.0, 1.0))
        aniso = core.plan(first, last, label_id=1, depth=4, spacing=(4.0, 1.0))
        self.assertTrue(iso.masks[2].any(), "test case must be non-degenerate")
        self.assertFalse(
            np.array_equal(iso.masks[2], aniso.masks[2]),
            "anisotropic spacing produced an identical result",
        )

    def test_anisotropy_direction_matches_geometry(self):
        """Stretching an axis must make the blend *less* permissive across it."""
        first, last = _shifted_discs(separation=10, radius=12)
        iso = core.plan(first, last, label_id=1, depth=4, spacing=(1.0, 1.0))
        # Stretch the axis the discs are NOT displaced along (rows/y): points
        # off-axis become farther, so the interpolated body narrows in y.
        stretched_y = core.plan(first, last, label_id=1, depth=4,
                                spacing=(4.0, 1.0))
        rows_iso = int(iso.masks[2].any(axis=1).sum())
        rows_agg = int(stretched_y.masks[2].any(axis=1).sum())
        self.assertLessEqual(
            rows_agg, rows_iso,
            "stretching y should not widen the result in y",
        )

    def test_anisotropic_endpoints_still_reproduce(self):
        case, z = load("anisotropy_endpoints")
        plan = core.plan(labels_from(z["first"]), labels_from(z["last"]),
                         label_id=1, depth=case["depth"],
                         spacing=tuple(case["spacing"]))
        first_mask = z["first"].astype(bool)
        for mask in plan.masks.values():
            np.testing.assert_array_equal(mask, first_mask)

    def test_uniform_scaling_does_not_change_the_result(self):
        """Scaling both axes equally rescales distances but not their signs."""
        case, z = load("cylinder_growing")
        first, last = labels_from(z["first"]), labels_from(z["last"])
        a = core.plan(first, last, label_id=1, depth=4, spacing=(1.0, 1.0))
        b = core.plan(first, last, label_id=1, depth=4, spacing=(3.0, 3.0))
        np.testing.assert_array_equal(a.masks[2], b.masks[2])

    def test_non_square_plane(self):
        first = np.zeros((32, 96), np.uint8); first[10:20, 30:60] = 1
        last = np.zeros((32, 96), np.uint8); last[12:18, 35:55] = 1
        plan = core.plan(first, last, label_id=1, depth=4)
        self.assertEqual(plan.masks[1].shape, (32, 96))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ValidationTests(TestCase):
    def setUp(self):
        self.a = np.zeros((32, 32), np.uint8); self.a[10:20, 10:20] = 1
        self.b = np.zeros((32, 32), np.uint8); self.b[12:18, 12:18] = 1

    def _err(self, **kw):
        params = dict(label_id=1, depth=4)
        params.update(kw)
        first = params.pop("first", self.a)
        last = params.pop("last", self.b)
        with self.assertRaises(InterpolationError) as ctx:
            core.plan(first, last, **params)
        return ctx.exception

    def test_shape_mismatch(self):
        self.assertEqual(
            self._err(last=np.zeros((16, 16), np.uint8)).reason,
            "shape_mismatch")

    def test_bad_rank(self):
        self.assertEqual(
            self._err(first=np.zeros((4, 4, 4), np.uint8),
                      last=np.zeros((4, 4, 4), np.uint8)).reason, "bad_rank")

    def test_background_label_is_reserved(self):
        self.assertEqual(self._err(label_id=0).reason, "reserved_label")

    def test_negative_label(self):
        self.assertEqual(self._err(label_id=-3).reason, "negative_label")

    def test_depth_too_small(self):
        self.assertEqual(self._err(depth=1).reason, "depth_too_small")

    def test_depth_too_large(self):
        self.assertEqual(
            self._err(depth=core.MAXIMUM_INTERPOLATION_DEPTH + 1).reason,
            "depth_too_large")

    def test_maximum_depth_is_the_specified_constant(self):
        self.assertEqual(core.MAXIMUM_INTERPOLATION_DEPTH, 100)
        self.assertEqual(core.MINIMUM_INTERPOLATION_DEPTH, 2)

    def test_non_finite_spacing(self):
        self.assertEqual(self._err(spacing=(float("nan"), 1.0)).reason,
                         "bad_spacing")

    def test_zero_spacing(self):
        self.assertEqual(self._err(spacing=(0.0, 1.0)).reason, "bad_spacing")

    def test_wrong_spacing_length(self):
        self.assertEqual(self._err(spacing=(1.0, 1.0, 1.0)).reason,
                         "bad_spacing")

    def test_unknown_overwrite_mode(self):
        self.assertEqual(self._err(overwrite_mode="clobber").reason,
                         "bad_overwrite_mode")

    def test_too_large(self):
        big = np.zeros((4096, 4096), np.uint8); big[0:10, 0:10] = 1
        with self.assertRaises(InterpolationError) as ctx:
            core.plan(big, big, label_id=1, depth=50, max_voxels=1000)
        self.assertEqual(ctx.exception.reason, "too_large")

    def test_missing_label_on_an_endpoint(self):
        empty = np.zeros((32, 32), np.uint8)
        with self.assertRaises(InterpolationError) as ctx:
            core.plan(self.a, empty, label_id=1, depth=4)
        self.assertEqual(ctx.exception.reason, "missing_endpoint_label")

    def test_empty_mask_signed_distance_is_refused(self):
        with self.assertRaises(InterpolationError) as ctx:
            core.signed_distance(np.zeros((8, 8), bool))
        self.assertEqual(ctx.exception.reason, "empty_mask")

    def test_full_mask_signed_distance_is_refused(self):
        with self.assertRaises(InterpolationError) as ctx:
            core.signed_distance(np.ones((8, 8), bool))
        self.assertEqual(ctx.exception.reason, "full_mask")

    def test_validation_precedes_computation(self):
        """A bad request must fail before any distance transform runs."""
        big = np.zeros((2048, 2048), np.uint8); big[0:4, 0:4] = 1
        with self.assertRaises(InterpolationError):
            core.plan(big, big, label_id=0, depth=4)  # reserved label


# ---------------------------------------------------------------------------
# Labels and overwrite modes
# ---------------------------------------------------------------------------


class LabelTests(TestCase):
    def setUp(self):
        self.mask = np.zeros((16, 16), bool); self.mask[4:12, 4:12] = True

    def test_only_the_active_label_is_interpolated(self):
        first = np.zeros((32, 32), np.uint8)
        first[5:15, 5:15] = 7
        first[20:25, 20:25] = 9
        last = np.zeros((32, 32), np.uint8)
        last[7:13, 7:13] = 7
        last[20:25, 20:25] = 9
        plan = core.plan(first, last, label_id=7, depth=4)
        # Label 9's region must not appear in the interpolated mask.
        self.assertFalse(plan.masks[2][20:25, 20:25].any())

    def test_overwrite_empty_preserves_existing_labels(self):
        labels = np.zeros((16, 16), np.uint8); labels[4:8, 4:8] = 5
        out = core.apply_to_slice(labels, self.mask, label_id=3,
                                  overwrite_mode=core.OVERWRITE_EMPTY)
        self.assertTrue((out[4:8, 4:8] == 5).all(), "existing label destroyed")
        self.assertTrue((out[9:12, 9:12] == 3).all())

    def test_overwrite_all_replaces(self):
        labels = np.zeros((16, 16), np.uint8); labels[4:8, 4:8] = 5
        out = core.apply_to_slice(labels, self.mask, label_id=3,
                                  overwrite_mode=core.OVERWRITE_ALL)
        self.assertTrue((out[4:8, 4:8] == 3).all())

    def test_default_mode_is_the_conservative_one(self):
        labels = np.zeros((16, 16), np.uint8); labels[4:8, 4:8] = 5
        out = core.apply_to_slice(labels, self.mask, label_id=3)
        self.assertTrue((out[4:8, 4:8] == 5).all())

    def test_input_is_never_mutated(self):
        labels = np.zeros((16, 16), np.uint8)
        before = labels.copy()
        core.apply_to_slice(labels, self.mask, label_id=3)
        np.testing.assert_array_equal(labels, before)

    def test_dtype_is_preserved(self):
        labels = np.zeros((16, 16), np.uint16)
        out = core.apply_to_slice(labels, self.mask, label_id=1000)
        self.assertEqual(out.dtype, np.uint16)

    def test_label_not_representable_in_dtype(self):
        labels = np.zeros((16, 16), np.uint8)
        with self.assertRaises(InterpolationError) as ctx:
            core.apply_to_slice(labels, self.mask, label_id=99999)
        self.assertEqual(ctx.exception.reason, "label_dtype_overflow")

    def test_maximum_uint8_label(self):
        labels = np.zeros((16, 16), np.uint8)
        out = core.apply_to_slice(labels, self.mask, label_id=255)
        self.assertEqual(int(out.max()), 255)

    def test_apply_shape_mismatch(self):
        with self.assertRaises(InterpolationError) as ctx:
            core.apply_to_slice(np.zeros((8, 8), np.uint8), self.mask,
                                label_id=1)
        self.assertEqual(ctx.exception.reason, "shape_mismatch")


# ---------------------------------------------------------------------------
# Service and Phase 7 integration
# ---------------------------------------------------------------------------


def make_user(name, role=UserRole.ANNOTATOR):
    u, _ = User.objects.get_or_create(username=name)
    u.set_password("pw-for-tests-1"); u.save()
    UserProfile.objects.update_or_create(user=u, defaults={"role": role})
    if role == UserRole.ANNOTATOR:
        AnnotatorProfile.objects.update_or_create(
            user=u, defaults={"is_active_annotator": True,
                              "max_active_tasks": 50})
    return User.objects.get(pk=u.pk)


class ServiceMixin:
    def build(self):
        self.user = make_user("interp-ann")
        self.project = Project.objects.create(title="Interp")
        self.dataset = Dataset.objects.create(project=self.project, name="ds")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="v",
            image_path="a.tif")
        self.task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume,
            z_start=0, z_end=8, y_end=64, x_end=64,
            assigned_to=self.user, status=TaskStatus.ASSIGNED)
        _, z = load("cylinder_growing")
        self.first = labels_from(z["first"])
        self.last = labels_from(z["last"])
        self.written = {}

    def write_slice(self, offset, mask):
        self.written[offset] = mask.copy()

    def make_plan(self, depth=4):
        return service.plan_interpolation(
            first_labels=self.first, last_labels=self.last,
            label_id=1, depth=depth)

    def apply(self, plan=None, **kw):
        params = dict(task=self.task, actor=self.user,
                      plan=plan or self.make_plan(), axis="z",
                      first_index=0, last_index=4,
                      write_slice=self.write_slice)
        params.update(kw)
        return service.apply_interpolation(**params)


@INTERP_OFF
class InterpolationDisabledTests(ServiceMixin, TestCase):
    def setUp(self):
        self.build()

    def test_disabled_by_default(self):
        self.assertFalse(service.interpolation_enabled())

    def test_plan_refuses(self):
        with self.assertRaises(InterpolationError) as ctx:
            self.make_plan()
        self.assertEqual(ctx.exception.reason, "disabled")

    def test_apply_refuses(self):
        with self.assertRaises(InterpolationError):
            self.apply(plan=object())

    def test_nothing_is_written_or_recorded(self):
        with self.assertRaises(InterpolationError):
            self.make_plan()
        self.assertEqual(self.written, {})
        self.assertEqual(AnnotationOperation.objects.count(), 0)


@INTERP_ON
class PlanOnlyTests(ServiceMixin, TestCase):
    """Planning is the preview: it must not touch anything."""

    def setUp(self):
        self.build()

    def test_plan_writes_nothing(self):
        plan = self.make_plan()
        self.assertEqual(len(plan.masks), 3)
        self.assertEqual(self.written, {})
        self.assertEqual(AnnotationOperation.objects.count(), 0)
        self.assertEqual(current_version(self.task), 0)

    def test_plan_reports_voxels_changed(self):
        self.assertGreater(self.make_plan().voxels_changed, 0)

    def test_apply_needs_the_operations_flag(self):
        """Applying records a Phase 7 operation, so it needs that flag too."""
        with self.assertRaises(InterpolationError) as ctx:
            self.apply()
        self.assertEqual(ctx.exception.reason, "operations_disabled")


@FULL_ON
class ApplyTests(ServiceMixin, TestCase):
    def setUp(self):
        self.build()

    def test_apply_records_exactly_one_operation(self):
        self.apply()
        self.assertEqual(AnnotationOperation.objects.count(), 1)

    def test_operation_kind_and_sequence(self):
        op = self.apply()
        self.assertEqual(op.kind, AnnotationOperation.Kind.PREDICT_COMMIT)
        self.assertEqual(op.seq, 1)
        self.assertEqual(current_version(self.task), 1)

    def test_all_intermediate_slices_are_written(self):
        self.apply()
        self.assertEqual(sorted(self.written), [1, 2, 3])

    def test_payload_records_provenance_without_voxels(self):
        op = self.apply()
        p = op.payload
        for key in ("algorithm", "algorithm_version", "axis", "first_index",
                    "last_index", "depth", "label_id", "spacing",
                    "overwrite_mode", "voxels_changed", "source_version"):
            self.assertIn(key, p)
        self.assertEqual(p["algorithm"], core.ALGORITHM_NAME)
        blob = json.dumps(p)
        self.assertLess(len(blob), 4096, "payload should stay small")
        # No dense voxel data anywhere in the payload.
        self.assertNotIn("mask", blob)
        self.assertNotIn("runs", blob)

    def test_payload_is_within_the_phase7_cap(self):
        from django.conf import settings as s

        op = self.apply(plan=self.make_plan(depth=50))
        size = len(json.dumps(op.payload).encode())
        self.assertLess(size, s.MITO_OP_PAYLOAD_MAX_BYTES)

    def test_idempotent_replay_returns_the_original(self):
        a = self.apply(idempotency_key="k1")
        self.written.clear()
        b = self.apply(idempotency_key="k1")
        self.assertEqual(a.id, b.id)
        self.assertEqual(AnnotationOperation.objects.count(), 1)
        self.assertEqual(self.written, {}, "replay must not re-apply")

    def test_same_key_different_parameters_is_rejected(self):
        self.apply(idempotency_key="k1")
        with self.assertRaises(InterpolationError) as ctx:
            self.apply(idempotency_key="k1", first_index=99)
        self.assertEqual(ctx.exception.reason, "idempotency_conflict")

    def test_expected_version_conflict(self):
        self.apply()
        with self.assertRaises(VersionConflict):
            self.apply(expected_version=0)

    def test_conflict_writes_nothing(self):
        self.apply()
        self.written.clear()
        with self.assertRaises(VersionConflict):
            self.apply(expected_version=0)
        self.assertEqual(self.written, {})
        self.assertEqual(current_version(self.task), 1)

    def test_locked_task_is_refused(self):
        self.task.annotation_locked = True
        self.task.save(update_fields=["annotation_locked"])
        with self.assertRaises(InterpolationError) as ctx:
            self.apply()
        self.assertEqual(ctx.exception.reason, "locked")

    def test_write_failure_rolls_back_the_operation(self):
        """No partial materialized state, and no orphaned operation row."""
        def explode(offset, mask):
            if offset == 2:
                raise RuntimeError("disk went away")
            self.written[offset] = mask

        with self.assertRaises(RuntimeError):
            self.apply(write_slice=explode)
        self.assertEqual(AnnotationOperation.objects.count(), 0)
        self.assertEqual(current_version(self.task), 0)

    def test_undo_marks_the_interpolation_undone(self):
        from annotation.operations import undo

        op = self.apply()
        inverse = undo(self.task, actor=self.user)
        op.refresh_from_db()
        self.assertIsNotNone(op.undone_at)
        self.assertEqual(inverse.inverse_of_id, op.id)

    def test_undo_does_not_restore_voxels(self):
        """Documented Phase 8 limit: Phase 7 deferred the snapshot store.

        Undo marks the operation reversed; it does not repaint the labels,
        because there is nothing to restore them from. Pinned so the limit is
        visible rather than discovered.
        """
        from annotation.operations import undo

        self.apply()
        written_before = dict(self.written)
        undo(self.task, actor=self.user)
        self.assertEqual(self.written, written_before)


@requires_postgres
@FULL_ON
class ConcurrentApplyTests(ServiceMixin, TransactionTestCase):
    def setUp(self):
        self.build()

    def test_concurrent_applies_produce_distinct_operations(self):
        import threading

        from django.db import connections

        errors, lock = [], threading.Lock()
        barrier = threading.Barrier(4, timeout=30)

        def worker(i):
            written = {}
            try:
                barrier.wait()
                service.apply_interpolation(
                    task=self.task, actor=self.user, plan=self.make_plan(),
                    axis="z", first_index=0, last_index=4,
                    write_slice=lambda o, m: written.__setitem__(o, m),
                )
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual([t for t in threads if t.is_alive()], [])
        self.assertEqual(errors, [])
        seqs = sorted(AnnotationOperation.objects.values_list("seq", flat=True))
        self.assertEqual(seqs, [1, 2, 3, 4], "sequence must stay dense")

    def test_concurrent_replay_of_one_key_applies_once(self):
        import threading

        from django.db import connections

        errors, lock = [], threading.Lock()
        barrier = threading.Barrier(4, timeout=30)

        def worker(i):
            try:
                barrier.wait()
                service.apply_interpolation(
                    task=self.task, actor=self.user, plan=self.make_plan(),
                    axis="z", first_index=0, last_index=4,
                    write_slice=lambda o, m: None, idempotency_key="same",
                )
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertEqual(AnnotationOperation.objects.count(), 1)
