"""Hard-case categories — recording, filtering, and re-filing.

The property that matters throughout: **blank is a real value**. Cases
recorded before the field existed carry none, and nothing anywhere may guess
one on their behalf.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import UserProfile
from annotation.models import AnnotationTask, HardCase
from annotation.services import (
    create_hard_case,
    normalize_hard_case_category,
    set_hard_case_category,
)
from core.choices import HardCaseCategory, TaskType, UserRole
from projects.models import Dataset, Project
from volumes.models import Volume


def make_user(username, role):
    user = User.objects.create_user(username=username, password="pw")
    UserProfile.objects.update_or_create(user=user, defaults={"role": role})
    # Refetch: the auto-created profile is cached on the instance, so every
    # `is_manager` check would otherwise read the stale default role.
    return User.objects.get(pk=user.pk)


class CategoryBaseTest(TestCase):
    def setUp(self):
        self.manager = make_user("mgr", UserRole.MANAGER)
        self.annotator = make_user("ann", UserRole.ANNOTATOR)
        self.stranger = make_user("out", UserRole.ANNOTATOR)
        self.project = Project.objects.create(title="P", created_by=self.manager)
        self.dataset = Dataset.objects.create(project=self.project, name="D")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="V",
            shape_z=2, shape_y=2, shape_x=2,
        )
        self.task = AnnotationTask.objects.create(
            project=self.project, volume=self.volume, assigned_to=self.annotator,
            z_start=0, z_end=2, y_end=2, x_end=2,
            task_type=TaskType.MANUAL_ANNOTATION,
        )

    def make_case(self, **kwargs):
        return create_hard_case(
            task=self.task, user=self.annotator, label_id=kwargs.pop("label_id", 3),
            **kwargs
        )


class VocabularyTests(CategoryBaseTest):
    def test_a_case_records_its_category(self):
        case = self.make_case(category=HardCaseCategory.NEEDS_SPLIT)
        self.assertEqual(case.category, HardCaseCategory.NEEDS_SPLIT)

    def test_a_case_without_one_is_blank_not_guessed(self):
        self.assertEqual(self.make_case().category, "")

    def test_an_unknown_category_is_refused(self):
        """A category the client thinks it set but the server dropped would
        make the inbox filter quietly lie about what is in it."""
        with self.assertRaises(ValueError):
            self.make_case(category="banana")

    def test_normalize_accepts_blank_as_a_real_value(self):
        self.assertEqual(normalize_hard_case_category(""), "")
        self.assertEqual(normalize_hard_case_category(None), "")

    def test_every_choice_is_accepted(self):
        for choice in HardCaseCategory:
            self.assertEqual(normalize_hard_case_category(choice.value), choice.value)


class RefilingTests(CategoryBaseTest):
    def test_the_recorder_can_refile_their_own_case(self):
        case = self.make_case(category=HardCaseCategory.UNCERTAIN)
        set_hard_case_category(
            case, user=self.annotator, category=HardCaseCategory.NEEDS_MERGE
        )
        case.refresh_from_db()
        self.assertEqual(case.category, HardCaseCategory.NEEDS_MERGE)

    def test_a_manager_can_refile_anyones_case(self):
        case = self.make_case(category=HardCaseCategory.UNCERTAIN)
        set_hard_case_category(case, user=self.manager, category=HardCaseCategory.OTHER)
        case.refresh_from_db()
        self.assertEqual(case.category, HardCaseCategory.OTHER)

    def test_a_bystander_cannot_refile(self):
        case = self.make_case(category=HardCaseCategory.UNCERTAIN)
        with self.assertRaises(PermissionError):
            set_hard_case_category(
                case, user=self.stranger, category=HardCaseCategory.OTHER
            )
        case.refresh_from_db()
        self.assertEqual(case.category, HardCaseCategory.UNCERTAIN)

    def test_it_can_be_cleared_back_to_uncategorised(self):
        """"We are not sure why" is a real answer and must stay reachable."""
        case = self.make_case(category=HardCaseCategory.UNCERTAIN)
        set_hard_case_category(case, user=self.annotator, category="")
        case.refresh_from_db()
        self.assertEqual(case.category, "")


class ApiTests(CategoryBaseTest):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def _cases(self, **params):
        response = self.client.get("/api/hard-cases/", params)
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_the_list_filters_by_category(self):
        self.make_case(label_id=1, category=HardCaseCategory.NEEDS_SPLIT)
        self.make_case(label_id=2, category=HardCaseCategory.UNCERTAIN)
        self.client.force_authenticate(self.manager)
        rows = self._cases(category=HardCaseCategory.NEEDS_SPLIT.value)
        self.assertEqual([r["label_id"] for r in rows], [1])

    def test_uncategorised_is_filterable(self):
        """Otherwise the cases nobody has filed are the ones you cannot find."""
        self.make_case(label_id=1, category=HardCaseCategory.NEEDS_SPLIT)
        self.make_case(label_id=2)
        self.client.force_authenticate(self.manager)
        rows = self._cases(category="uncategorised")
        self.assertEqual([r["label_id"] for r in rows], [2])

    def test_no_filter_returns_everything(self):
        self.make_case(label_id=1, category=HardCaseCategory.NEEDS_SPLIT)
        self.make_case(label_id=2)
        self.client.force_authenticate(self.manager)
        self.assertEqual(len(self._cases()), 2)

    def test_the_category_is_serialized(self):
        self.make_case(category=HardCaseCategory.FALSE_POSITIVE)
        self.client.force_authenticate(self.manager)
        self.assertEqual(
            self._cases()[0]["category"], HardCaseCategory.FALSE_POSITIVE.value
        )

    def test_the_edit_endpoint_sets_the_category(self):
        case = self.make_case()
        self.client.force_authenticate(self.annotator)
        response = self.client.patch(
            f"/api/hard-cases/{case.pk}/note/",
            {"category": HardCaseCategory.NEEDS_MERGE.value}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["category"], HardCaseCategory.NEEDS_MERGE.value)

    def test_the_edit_endpoint_still_sets_the_note_alone(self):
        case = self.make_case()
        self.client.force_authenticate(self.annotator)
        response = self.client.patch(
            f"/api/hard-cases/{case.pk}/note/", {"note": "hi"}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["note"], "hi")

    def test_an_empty_patch_is_refused(self):
        case = self.make_case()
        self.client.force_authenticate(self.annotator)
        response = self.client.patch(
            f"/api/hard-cases/{case.pk}/note/", {}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_a_bad_category_is_a_400_not_a_500(self):
        self.client.force_authenticate(self.annotator)
        response = self.client.patch(
            f"/api/hard-cases/{self.make_case().pk}/note/",
            {"category": "banana"}, format="json",
        )
        self.assertEqual(response.status_code, 400)
