"""Per-instance morphology and QA flags.

The property under test throughout is the one the whole module rests on:
**an absent row means "not annotated"**, and it must stay distinguishable from
every other state — especially from `normal`, which is a positive finding.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import UserProfile
from annotation.instance_annotations import (
    normalize_qa_flags,
    project_summary,
    set_instance_annotation,
)
from annotation.models import AnnotationTask, LabelInstanceAnnotation
from core.choices import InstanceQaFlag, MitoMorphology, TaskType, UserRole
from projects.models import Dataset, Project
from volumes.models import Volume

ENABLED = override_settings(FEATURE_INSTANCE_ANNOTATION=True)


def make_user(username, role):
    user = User.objects.create_user(username=username, password="pw")
    UserProfile.objects.update_or_create(user=user, defaults={"role": role})
    # Refetch: the profile is auto-created with the default annotator role and
    # cached on the instance, so `update_or_create` changes the row while this
    # object still reports "annotator" — and every `is_manager` check reading
    # it would silently test the wrong role. Same trap documented in
    # volumes/test_chunk_service.py.
    return User.objects.get(pk=user.pk)


class InstanceAnnotationBaseTest(TestCase):
    def setUp(self):
        self.manager = make_user("mgr", UserRole.MANAGER)
        self.annotator = make_user("ann", UserRole.ANNOTATOR)
        self.outsider = make_user("out", UserRole.ANNOTATOR)
        self.project = Project.objects.create(title="P", created_by=self.manager)
        self.dataset = Dataset.objects.create(project=self.project, name="D")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="V",
            shape_z=4, shape_y=4, shape_x=4,
        )
        self.task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume, assigned_to=self.annotator,
            z_start=0, z_end=4, y_end=4, x_end=4,
            task_type=TaskType.MANUAL_ANNOTATION,
        )


class ServiceTests(InstanceAnnotationBaseTest):
    def test_an_unannotated_instance_has_no_row(self):
        self.assertEqual(LabelInstanceAnnotation.objects.count(), 0)

    def test_setting_then_clearing_leaves_no_row_behind(self):
        """A row that carries nothing must not linger.

        "Has a row" is used as a proxy for "somebody recorded something", so an
        emptied row would make the summary counts overstate the work done.
        """
        set_instance_annotation(
            self.volume, 7, actor=self.annotator, morphology=MitoMorphology.SWOLLEN
        )
        self.assertEqual(LabelInstanceAnnotation.objects.count(), 1)

        row = set_instance_annotation(self.volume, 7, actor=self.annotator, morphology="")
        self.assertIsNone(row)
        self.assertEqual(LabelInstanceAnnotation.objects.count(), 0)

    def test_omitted_fields_are_left_alone(self):
        """The panel sends one control at a time; the others must survive.

        Treating "not sent" as "clear" wiped the note on every morphology
        click.
        """
        set_instance_annotation(
            self.volume, 3, actor=self.annotator,
            morphology=MitoMorphology.DONUT, note="ring shaped",
            qa_flags=[InstanceQaFlag.UNCERTAIN],
        )
        set_instance_annotation(
            self.volume, 3, actor=self.annotator, morphology=MitoMorphology.MEGA
        )
        row = LabelInstanceAnnotation.objects.get(volume=self.volume, label_id=3)
        self.assertEqual(row.morphology, MitoMorphology.MEGA)
        self.assertEqual(row.note, "ring shaped")
        self.assertEqual(row.qa_flags, [InstanceQaFlag.UNCERTAIN.value])

    def test_the_two_dimensions_are_independent(self):
        """A mitochondrion can be both swollen and uncertain."""
        row = set_instance_annotation(
            self.volume, 1, actor=self.annotator,
            morphology=MitoMorphology.SWOLLEN,
            qa_flags=[InstanceQaFlag.UNCERTAIN],
        )
        self.assertEqual(row.morphology, MitoMorphology.SWOLLEN)
        self.assertIn(InstanceQaFlag.UNCERTAIN.value, row.qa_flags)

    def test_background_cannot_be_annotated(self):
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                set_instance_annotation(self.volume, bad, actor=self.annotator)

    def test_an_unknown_flag_is_refused_not_dropped(self):
        """Silently dropping a flag would read downstream as "never raised"."""
        with self.assertRaises(ValueError):
            normalize_qa_flags(["uncertain", "not_a_real_flag"])

    def test_flags_are_stored_in_a_canonical_order(self):
        first = normalize_qa_flags(
            [InstanceQaFlag.FALSE_POSITIVE, InstanceQaFlag.UNCERTAIN]
        )
        second = normalize_qa_flags(
            [InstanceQaFlag.UNCERTAIN, InstanceQaFlag.FALSE_POSITIVE]
        )
        self.assertEqual(first, second)

    def test_duplicate_flags_collapse(self):
        self.assertEqual(
            normalize_qa_flags(["uncertain", "uncertain"]), ["uncertain"]
        )

    def test_summary_separates_unclassified_from_normal(self):
        """The distinction that justifies a blank morphology existing at all."""
        set_instance_annotation(
            self.volume, 1, actor=self.annotator, morphology=MitoMorphology.NORMAL
        )
        # Flagged, but nobody said what shape it is.
        set_instance_annotation(
            self.volume, 2, actor=self.annotator,
            qa_flags=[InstanceQaFlag.UNCERTAIN],
        )
        summary = project_summary(self.project)
        self.assertEqual(summary["morphology"][MitoMorphology.NORMAL.value], 1)
        self.assertEqual(summary["morphology_unclassified"], 1)
        self.assertEqual(summary["qa_flags"][InstanceQaFlag.UNCERTAIN.value], 1)
        self.assertEqual(summary["total_annotated"], 2)

    def test_review_worthy_reflects_the_flags(self):
        truncated = set_instance_annotation(
            self.volume, 4, actor=self.annotator,
            qa_flags=[InstanceQaFlag.BOUNDARY_TRUNCATED],
        )
        # Truncation is a fact about the data, not a request for a second look.
        self.assertFalse(truncated.review_worthy)
        unsure = set_instance_annotation(
            self.volume, 5, actor=self.annotator,
            qa_flags=[InstanceQaFlag.NEEDS_SPLIT],
        )
        self.assertTrue(unsure.review_worthy)


@ENABLED
class ApiTests(InstanceAnnotationBaseTest):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def test_the_assignee_may_write(self):
        self.client.force_authenticate(self.annotator)
        response = self.client.put(
            f"/api/tasks/{self.task.pk}/instance-annotations/9/",
            {"morphology": "swollen", "qa_flags": ["uncertain"]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["morphology"], "swollen")
        self.assertTrue(response.data["review_worthy"])

    def test_an_unrelated_annotator_may_not_write(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.put(
            f"/api/tasks/{self.task.pk}/instance-annotations/9/",
            {"morphology": "swollen"}, format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(LabelInstanceAnnotation.objects.count(), 0)

    def test_an_invalid_morphology_is_a_400(self):
        self.client.force_authenticate(self.annotator)
        response = self.client.put(
            f"/api/tasks/{self.task.pk}/instance-annotations/9/",
            {"morphology": "banana"}, format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_clearing_returns_the_empty_shape_rather_than_204(self):
        """So the client can update its map without a second request."""
        self.client.force_authenticate(self.annotator)
        self.client.put(
            f"/api/tasks/{self.task.pk}/instance-annotations/9/",
            {"morphology": "swollen"}, format="json",
        )
        response = self.client.put(
            f"/api/tasks/{self.task.pk}/instance-annotations/9/",
            {"morphology": "", "qa_flags": [], "note": ""}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["morphology"], "")
        self.assertEqual(response.data["qa_flags"], [])

    def test_the_response_only_lists_annotated_instances(self):
        self.client.force_authenticate(self.annotator)
        self.client.put(
            f"/api/tasks/{self.task.pk}/instance-annotations/9/",
            {"morphology": "mega"}, format="json",
        )
        response = self.client.get(
            f"/api/tasks/{self.task.pk}/instance-annotations/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["annotations"]), 1)
        self.assertEqual(response.data["annotations"][0]["label_id"], 9)

    def test_the_vocabulary_is_served_so_the_client_never_hardcodes_it(self):
        self.client.force_authenticate(self.annotator)
        response = self.client.get(
            f"/api/tasks/{self.task.pk}/instance-annotations/"
        )
        served = {item["value"] for item in response.data["vocabulary"]["morphology"]}
        self.assertEqual(served, {choice.value for choice in MitoMorphology})


class DisabledTests(InstanceAnnotationBaseTest):
    def test_endpoints_report_disabled_rather_than_missing(self):
        """A flag that produced a 404 would be indistinguishable from a typo."""
        client = APIClient()
        client.force_authenticate(self.annotator)
        with override_settings(FEATURE_INSTANCE_ANNOTATION=False):
            response = client.get(
                f"/api/tasks/{self.task.pk}/instance-annotations/"
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["reason"], "disabled")
